"""API for the Focus HUD: tasks broken into steps, quick capture, voice modes."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from openjarvis.focus import commands, mac_calendar, mac_mail, spanish
from openjarvis.focus.signals import (
    CONTRACTS,
    max_stop_points,
    normalize_symbol,
    plan_trade,
)
from openjarvis.focus.store import FocusStore

logger = logging.getLogger(__name__)

focus_router = APIRouter(prefix="/v1/focus", tags=["focus"])

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}

# Two original Kokoro voices: a slow, low one by default and a brisk, firm
# one once a deadline has passed.
VOICE_MODES = {
    "calm": ("bm_george", 0.88),
    "deadline": ("am_eric", 1.1),
    "es": ("em_alex", 0.85),
}

_BREAKDOWN_PROMPT = (
    "Break the task below into 3 to 6 tiny, concrete, physical steps for "
    "someone with ADHD. Each step starts with a verb, takes under five "
    "minutes, and is at most eight words. Reply with only a JSON array of "
    "strings.\n\nTask: {title}"
)


class TaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    due: Optional[str] = None
    steps: Optional[List[str]] = None


class CaptureBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    kind: str = Field(default="note", pattern=r"^(note|idea)$")


class RiskBody(BaseModel):
    risk_per_trade: float = Field(gt=0, le=100000)


class AlertBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    side: str
    price: float = Field(gt=0)
    atr: Optional[float] = Field(default=None, gt=0)
    token: Optional[str] = None


class JobProfileBody(BaseModel):
    titles: Optional[List[str]] = None
    min_salary: Optional[int] = Field(default=None, ge=0)
    resume: Optional[str] = Field(default=None, max_length=40000)


class JobBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    company: str = Field(default="", max_length=200)
    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")
    salary: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=20000)


class JobStatusBody(BaseModel):
    status: str = Field(pattern=r"^(found|drafted|submitted|skipped)$")


class SpanishAnswerBody(BaseModel):
    item_id: str = Field(min_length=1, max_length=60)
    action: str = Field(pattern=r"^(seen|answer|skip|skip_unit)$")
    text: str = Field(default="", max_length=200)


class ConverseTurn(BaseModel):
    role: str = Field(pattern=r"^(you|tutor)$")
    text: str = Field(max_length=600)


class ConverseBody(BaseModel):
    text: str = Field(default="", max_length=600)
    history: List[ConverseTurn] = Field(default_factory=list, max_length=12)


class GoalBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    target: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)


class GoalEntryBody(BaseModel):
    amount: float = Field(ge=-1_000_000, le=1_000_000)
    note: str = Field(default="", max_length=120)


class CommandBody(BaseModel):
    text: str = Field(max_length=600)
    # True when the wake word was heard just before, so this is the command.
    awake: bool = False
    # Set when Jarvis asked a follow-up question and this is the answer.
    pending: str = Field(default="", max_length=300)


class CalendarBody(BaseModel):
    # Name of the calendar new dated tasks are copied to; empty turns it off.
    write_to: str = Field(default="", max_length=200)


class KeyBody(BaseModel):
    key: str = Field(default="", max_length=400)


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    mode: str = "calm"
    # Which calm voice to use; ignored for the deadline and Spanish voices.
    voice: Optional[str] = None


# Deep, calm male voices the person can choose between for everyday replies.
CALM_VOICES = ("bm_george", "am_onyx", "am_michael", "bm_lewis", "am_fenrir")


def _store(request: Request) -> FocusStore:
    store = getattr(request.app.state, "focus_store", None)
    if store is None:
        store = FocusStore()
        request.app.state.focus_store = store
    return store


def parse_steps(text: str) -> List[str]:
    """Pull a list of step strings out of a model reply."""
    match = re.search(r"\[.*\]", text or "", re.DOTALL)
    if match:
        try:
            items = json.loads(match.group(0))
            steps = [str(i).strip() for i in items if str(i).strip()]
            if steps:
                return steps[:8]
        except ValueError:
            pass
    lines = [
        re.sub(r"^[\s\-\*\d\.\)]+", "", ln).strip() for ln in (text or "").splitlines()
    ]
    return [ln for ln in lines if re.search(r"[A-Za-z]{2}", ln)][:8]


# Online model, used when an Anthropic key is saved on this computer. Without a
# key the local model answers instead.
CLOUD_MODEL = os.environ.get("OPENJARVIS_FOCUS_MODEL", "claude-opus-5-5")
# Models that accept a server-side fallback when a request is declined.
_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


class NoModelError(RuntimeError):
    """Neither an online key nor a local model is available."""


def _api_key() -> str:
    from openjarvis.server import cloud_router

    return cloud_router._load_keys().get("ANTHROPIC_API_KEY", "")


async def _claude(api_key: str, prompt: str) -> str:
    """One reply from Claude through the official SDK. Raises SDK errors."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=60.0, max_retries=1)
    request = {
        "model": CLOUD_MODEL,
        "max_tokens": 16000,
        # These replies are short and spoken aloud, so favour a quick answer.
        "output_config": {"effort": "low"},
        "messages": [{"role": "user", "content": prompt}],
    }
    if CLOUD_MODEL.startswith(_FALLBACK_MODELS):
        # If the model declines a request, let the API retry it on a fallback
        # model inside the same call instead of returning nothing.
        response = await client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request
        )
    else:
        response = await client.messages.create(**request)
    if response.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in response.content if b.type == "text").strip()


async def _complete(
    request: Request, prompt: str, *, temperature: float, max_tokens: int
) -> str:
    """One model reply: the online model when a key is saved, else local."""
    from openjarvis.core.types import Message, Role

    api_key = _api_key()
    if api_key:
        try:
            text = await _claude(api_key, prompt)
            if text:
                return text
        except Exception:
            logger.exception("Online model call failed; trying the local model")
    engine = getattr(request.app.state, "engine", None)
    model = getattr(request.app.state, "model", None)
    if engine is None or not model:
        raise NoModelError("No model is configured")
    result = await asyncio.to_thread(
        engine.generate,
        [Message(role=Role.USER, content=prompt)],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (result.get("content") or "").strip()


def _online(request: Request) -> bool:
    return bool(_api_key())


async def _breakdown(request: Request, title: str) -> List[str]:
    # The small local model is slow on a laptop and its steps are poor, and
    # running it competes with speech recognition. Without the online model a
    # to-do is simply its own single step.
    local_ok = getattr(request.app.state, "focus_local_steps", False)
    if not (_online(request) or local_ok):
        return [title]
    try:
        text = await _complete(
            request,
            _BREAKDOWN_PROMPT.format(title=title),
            temperature=0.2,
            max_tokens=300,
        )
        return parse_steps(text) or [title]
    except Exception:
        logger.exception("Task breakdown failed; using the task as a single step")
        return [title]


@focus_router.get("/state")
async def focus_state(request: Request):
    return _store(request).snapshot()


@focus_router.post("/task")
async def add_task(request: Request, body: TaskBody):
    title = body.title.strip()
    steps = [s.strip() for s in (body.steps or []) if s.strip()]
    if not steps:
        steps = await _breakdown(request, title)
    task = _store(request).add_task(title, steps, body.due)
    _copy_to_calendar(request, title, body.due)
    return task


@focus_router.post("/task/{task_id}/step")
async def step_done(request: Request, task_id: str):
    task = _store(request).advance(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


class DoneBody(BaseModel):
    done: bool = True


@focus_router.post("/task/{task_id}/done")
async def set_task_done(request: Request, task_id: str, body: DoneBody):
    task = _store(request).set_done(task_id, body.done)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@focus_router.delete("/task/{task_id}")
async def delete_task(request: Request, task_id: str):
    if not _store(request).remove_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


@focus_router.post("/capture")
async def add_capture(request: Request, body: CaptureBody):
    return _store(request).add_capture(body.text.strip(), kind=body.kind)


@focus_router.delete("/capture/{capture_id}")
async def delete_capture(request: Request, capture_id: str):
    if not _store(request).remove_capture(capture_id):
        raise HTTPException(status_code=404, detail="Capture not found")
    return {"ok": True}


@focus_router.put("/risk")
async def set_risk(request: Request, body: RiskBody):
    return {"risk_per_trade": _store(request).set_risk(body.risk_per_trade)}


def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK:
        raise HTTPException(status_code=403, detail="Only from this computer")


def _write_key(value: str) -> None:
    """Store or remove the Anthropic key in the per-user keys file."""
    from openjarvis.server import cloud_router

    path = cloud_router._CLOUD_ENV_FILE
    lines = []
    if path.exists():
        lines = [
            ln
            for ln in path.read_text().splitlines()
            if not ln.strip().startswith("ANTHROPIC_API_KEY=")
        ]
    if value:
        lines.append(f"ANTHROPIC_API_KEY={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    path.chmod(0o600)


def _background(request: Request, coro) -> None:
    job = asyncio.create_task(coro)
    jobs = getattr(request.app.state, "focus_jobs", None)
    if jobs is None:
        jobs = request.app.state.focus_jobs = set()
    jobs.add(job)
    job.add_done_callback(jobs.discard)


def _copy_to_calendar(request: Request, title: str, due: Optional[str]) -> None:
    """Put a dated task on the chosen Mac calendar, without holding up the reply."""
    target = (_store(request).section("calendar") or {}).get("write_to", "")
    if not (due and target and mac_calendar.available()):
        return
    try:
        when = datetime.fromisoformat(due)
    except ValueError:
        return
    # 23:59 is how "that day, no particular time" is stored.
    all_day = (when.hour, when.minute) == (23, 59)
    if all_day:
        when = when.replace(hour=0, minute=0)

    async def _add() -> None:
        try:
            await asyncio.to_thread(
                mac_calendar.create_event, target, title, when, 30, all_day
            )
            slot = getattr(request.app.state, "focus_calendar_slot", None)
            if slot:
                slot["at"] = 0.0  # show the new event on the next read
            request.app.state.focus_calendar_error = ""
        except Exception as exc:
            logger.warning("Could not add %r to the calendar: %s", title, exc)
            request.app.state.focus_calendar_error = str(exc)

    _background(request, _add())


def _slow_read(request: Request, name: str, loader, ttl: float) -> dict:
    """Latest result of a slow read, refreshed in the background.

    The Mac's Calendar and Mail can take a minute to answer the first time,
    so a request never waits for them: it gets what is known so far and a
    ``loading`` flag, and the read carries on behind it.
    """
    state = request.app.state
    slot = getattr(state, f"focus_{name}_slot", None)
    if slot is None:
        slot = {"at": 0.0, "data": None, "error": "", "task": None}
        setattr(state, f"focus_{name}_slot", slot)
    age = time.monotonic() - slot["at"]
    stale = slot["at"] == 0.0 or age > (ttl if not slot["error"] else 60)
    running = slot["task"] is not None and not slot["task"].done()
    if stale and not running:

        async def _run() -> None:
            try:
                slot["data"] = await asyncio.to_thread(loader)
                slot["error"] = ""
            except mac_calendar.CalendarError as exc:
                slot["error"] = str(exc)
            except Exception:
                logger.exception("Reading %s failed", name)
                slot["error"] = f"The {name} could not be read."
            slot["at"] = time.monotonic()

        slot["task"] = asyncio.create_task(_run())
        running = True
    return {
        "data": slot["data"],
        "error": slot["error"],
        "loading": running and slot["data"] is None and not slot["error"],
    }


def _unique_events(events: list) -> list:
    """Drop an event that appears on more than one calendar at the same time."""
    seen, out = set(), []
    for event in events:
        key = (
            str(event.get("title", "")).strip().lower(),
            str(event.get("start"))[:16],
        )
        if key not in seen:
            seen.add(key)
            out.append(event)
    return out


@focus_router.get("/mail")
async def unread_mail(request: Request):
    """Unread mail from the Mac's Mail app. Subjects are not written to disk."""
    out = {"available": mac_mail.available(), "unread": 0, "accounts": [], "latest": []}
    out.update(error="", loading=False)
    if not out["available"]:
        return out
    got = _slow_read(request, "mail", mac_mail.read_unread, 300)
    return {
        **out,
        **(got["data"] or {}),
        "error": got["error"],
        "loading": got["loading"],
    }


def _load_calendar() -> dict:
    data = mac_calendar.read_events()
    data["events"] = _unique_events(data["events"])
    data["calendars"] = mac_calendar.calendars()
    return data


@focus_router.get("/calendar")
async def calendar_events(request: Request):
    """Upcoming events from the Mac's Calendar app, refreshed every two minutes."""
    state = request.app.state
    write_to = (_store(request).section("calendar") or {}).get("write_to", "")
    out = {
        "available": mac_calendar.available(),
        "events": [],
        "calendars": [],
        "write_to": write_to,
        "source": "none",
        "note": "",
        "error": getattr(state, "focus_calendar_error", ""),
        "loading": False,
    }
    if not out["available"]:
        return out
    got = _slow_read(request, "calendar", _load_calendar, 120)
    return {
        **out,
        **(got["data"] or {}),
        "error": got["error"] or out["error"],
        "loading": got["loading"],
    }


@focus_router.put("/calendar")
async def set_calendar(request: Request, body: CalendarBody):
    _store(request).save_section("calendar", {"write_to": body.write_to.strip()})
    return {"write_to": body.write_to.strip()}


@focus_router.get("/key")
async def key_status(request: Request):
    """Whether an online key is saved. The key itself is never returned."""
    return {"configured": bool(_api_key()), "model": CLOUD_MODEL}


@focus_router.put("/key")
async def save_key(request: Request, body: KeyBody):
    _local_only(request)
    key = body.key.strip()
    if key and (not key.startswith("sk-ant-") or len(key) < 20 or " " in key):
        raise HTTPException(
            status_code=400, detail="That does not look like an Anthropic API key"
        )
    _write_key(key)
    return {"configured": bool(_api_key()), "model": CLOUD_MODEL}


@focus_router.post("/key/test")
async def test_key(request: Request):
    """Make one tiny request with the saved key and report what happened."""
    _local_only(request)
    api_key = _api_key()
    if not api_key:
        return {"ok": False, "error": "No key is saved yet."}
    import anthropic

    try:
        text = await _claude(api_key, "Reply with the single word: ready")
    except anthropic.AuthenticationError:
        return {
            "ok": False,
            "error": "The key was rejected. Check you copied all of it.",
        }
    except anthropic.PermissionDeniedError:
        return {"ok": False, "error": "This key is not allowed to use that model."}
    except anthropic.NotFoundError:
        return {"ok": False, "error": f"The model {CLOUD_MODEL} was not found."}
    except anthropic.RateLimitError:
        return {"ok": False, "error": "Rate limited. Wait a minute and test again."}
    except anthropic.APIStatusError as exc:
        detail = getattr(exc, "message", "") or f"error {exc.status_code}"
        return {"ok": False, "error": f"The service answered: {detail}"}
    except anthropic.APIConnectionError:
        return {
            "ok": False,
            "error": "Could not reach the service. Check the internet.",
        }
    return {
        "ok": bool(text),
        "model": CLOUD_MODEL,
        "error": "" if text else "Empty reply.",
    }


@focus_router.put("/goal")
async def set_goal(request: Request, body: GoalBody):
    name = body.name.strip() if body.name else None
    return _store(request).set_goal(name=name or None, target=body.target)


@focus_router.post("/goal/entry")
async def add_goal_entry(request: Request, body: GoalEntryBody):
    if body.amount == 0:
        raise HTTPException(status_code=400, detail="Enter an amount other than 0")
    return _store(request).add_goal_entry(body.amount, body.note.strip())


@focus_router.delete("/goal/entry/{entry_id}")
async def delete_goal_entry(request: Request, entry_id: str):
    if not _store(request).remove_goal_entry(entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


@focus_router.get("/sizing")
async def sizing(request: Request):
    """Per contract: the widest stop one contract can carry at the risk limit."""
    risk = float(_store(request).snapshot()["risk_per_trade"])
    rows = []
    for c in CONTRACTS.values():
        rows.append(
            {
                "symbol": c.symbol,
                "name": c.name,
                "point_value": c.point_value,
                "tick_size": c.tick_size,
                "max_stop_points": max_stop_points(c.symbol, risk),
            }
        )
    return {"risk_per_trade": risk, "feed_connected": False, "contracts": rows}


_COVER_PROMPT = (
    "Write a cover note of at most 130 words for the job below. Use only facts "
    "found in the resume. Do not state or imply where the applicant lives or "
    "works from. Plain text, no greeting line, no placeholders.\n\n"
    "JOB: {title} at {company}\n{description}\n\nRESUME:\n{resume}"
)


async def _draft_cover(request: Request, job: dict, resume: str) -> str:
    prompt = _COVER_PROMPT.format(
        title=job["title"],
        company=job.get("company") or "the company",
        description=(job.get("description") or "")[:6000],
        resume=resume[:12000],
    )
    try:
        text = await _complete(request, prompt, temperature=0.4, max_tokens=400)
    except NoModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Cover note draft failed")
        raise HTTPException(status_code=502, detail="The model call failed") from exc
    if not text:
        raise HTTPException(status_code=502, detail="The model returned nothing")
    return text


@focus_router.put("/jobs/profile")
async def set_job_profile(request: Request, body: JobProfileBody):
    profile = _store(request).set_job_profile(
        titles=body.titles, min_salary=body.min_salary, resume=body.resume
    )
    return {
        "titles": profile["titles"],
        "min_salary": profile["min_salary"],
        "has_resume": bool(profile.get("resume")),
    }


def _resume_text(name: str, raw: bytes) -> str:
    """Plain text from an uploaded resume (.docx, .txt or .md)."""
    lower = name.lower()
    if lower.endswith(".docx"):
        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as doc:
                xml = doc.read("word/document.xml").decode("utf-8", "replace")
        except (KeyError, zipfile.BadZipFile) as exc:
            raise HTTPException(
                status_code=400, detail="That Word file could not be read"
            ) from exc
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
        import html

        return html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()
    if lower.endswith((".txt", ".md")):
        return raw.decode("utf-8", "replace").strip()
    raise HTTPException(
        status_code=400, detail="Use a Word (.docx) or text (.txt) file"
    )


_TITLES_PROMPT = (
    "From the resume below, suggest the five job titles this person should "
    "search for. Reply with only a JSON array of strings.\n\nRESUME:\n{resume}"
)


@focus_router.post("/jobs/resume")
async def upload_resume(request: Request):
    """Store the resume text on this computer; suggest titles if none are set."""
    _local_only(request)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="Missing 'file' field")
    raw = await upload.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That file is too large")
    text = _resume_text(getattr(upload, "filename", "") or "", raw)[:40000]
    if len(text) < 50:
        raise HTTPException(status_code=400, detail="No text was found in that file")
    store = _store(request)
    profile = store.set_job_profile(resume=text)
    if not profile.get("titles") and _api_key():
        try:
            reply = await _claude(
                _api_key(), _TITLES_PROMPT.format(resume=text[:12000])
            )
            titles = [t for t in parse_steps(reply) if len(t) < 80][:6]
            if titles:
                profile = store.set_job_profile(titles=titles)
        except Exception:
            logger.exception("Could not suggest job titles")
    return {
        "titles": profile["titles"],
        "min_salary": profile["min_salary"],
        "has_resume": True,
    }


_SEARCH_PROMPT = (
    "Search the web for current job postings that match all of this:\n"
    "- Title similar to one of: {titles}\n"
    "- Fully remote, open to people in the United States\n"
    "- {salary}\n"
    "Open the postings to confirm they are still accepting applications. Reply "
    "with only a JSON array of up to 8 objects with the fields title, company, "
    "url (the posting's own page), salary (as written, or an empty string) and "
    "description (one sentence). Leave out anything you could not confirm."
)


async def _claude_search(api_key: str, prompt: str) -> str:
    """One request with the hosted web search tool; resumes paused turns."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=300.0, max_retries=1)
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]
    messages = [{"role": "user", "content": prompt}]
    response = None
    for _ in range(5):
        response = await client.messages.create(
            model=CLOUD_MODEL, max_tokens=16000, tools=tools, messages=messages
        )
        if response.stop_reason != "pause_turn":
            break
        # A long search can pause; sending the turn back lets it continue.
        messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": response.content},
        ]
    if response is None or response.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in response.content if b.type == "text").strip()


@focus_router.post("/jobs/search")
async def search_jobs(request: Request):
    """Find postings with the online model's web search and queue the new ones."""
    _local_only(request)
    api_key = _api_key()
    if not api_key:
        raise HTTPException(status_code=409, detail="Add the online model key first")
    store = _store(request)
    profile = store.job_profile()
    titles = [t for t in profile.get("titles", []) if t]
    if not titles:
        raise HTTPException(status_code=409, detail="Add the job titles to search for")
    floor = int(profile.get("min_salary") or 0)
    salary = f"Pays at least ${floor:,} a year" if floor else "Any salary"
    try:
        reply = await _claude_search(
            api_key, _SEARCH_PROMPT.format(titles=", ".join(titles), salary=salary)
        )
    except Exception as exc:
        logger.exception("Job search failed")
        raise HTTPException(
            status_code=502, detail="The search did not finish"
        ) from exc
    match = re.search(r"\[.*\]", reply, re.DOTALL)
    try:
        found = json.loads(match.group(0)) if match else []
    except ValueError:
        found = []
    before = {j["url"] for j in store.snapshot()["jobs"]}
    added = 0
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not (title and re.match(r"^https?://", url)) or url in before:
            continue
        store.add_job(
            {
                "title": title[:200],
                "company": str(item.get("company", ""))[:200],
                "url": url[:2000],
                "salary": str(item.get("salary", ""))[:100],
                "description": str(item.get("description", ""))[:2000],
            }
        )
        before.add(url)
        added += 1
    return {"added": added, "found": len(found) if isinstance(found, list) else 0}


_BUSINESS_PROMPT = (
    "Suggest three small businesses or paid services this person could start "
    "alone, online, with little money, based on the resume and their own "
    "ideas below. For each give: name (a few words), why (one sentence on why "
    "it fits them), and steps (three small first actions that test whether "
    "people will pay, each doable in under an hour). Do not promise income. "
    "Reply with only a JSON array of objects with the fields name, why, steps."
    "\n\nTHEIR IDEAS:\n{ideas}\n\nRESUME:\n{resume}"
)


@focus_router.get("/business")
async def business_plans(request: Request):
    return _store(request).section("business") or {"plans": []}


@focus_router.post("/business/suggest")
async def suggest_businesses(request: Request):
    """Ask the online model for business ideas that fit the saved resume."""
    _local_only(request)
    api_key = _api_key()
    if not api_key:
        raise HTTPException(status_code=409, detail="Add the online model key first")
    store = _store(request)
    resume = store.job_profile().get("resume") or ""
    if not resume:
        raise HTTPException(
            status_code=409, detail="Load your resume on the Job Hunt page"
        )
    ideas = [c["text"] for c in store.snapshot()["captures"] if c.get("kind") == "idea"]
    prompt = _BUSINESS_PROMPT.format(
        ideas="\n".join(f"- {i}" for i in ideas[:20]) or "(none yet)",
        resume=resume[:12000],
    )
    try:
        reply = await _claude(api_key, prompt)
        match = re.search(r"\[.*\]", reply, re.DOTALL)
        raw = json.loads(match.group(0)) if match else []
    except Exception as exc:
        logger.exception("Business suggestions failed")
        raise HTTPException(
            status_code=502, detail="The suggestions did not arrive"
        ) from exc
    plans = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            continue
        steps = item.get("steps") if isinstance(item.get("steps"), list) else []
        plans.append(
            {
                "name": str(item["name"]).strip()[:120],
                "why": str(item.get("why", "")).strip()[:400],
                "steps": [str(x).strip()[:200] for x in steps if str(x).strip()][:5],
            }
        )
    if not plans:
        raise HTTPException(status_code=502, detail="No usable suggestions came back")
    data = {"plans": plans[:5]}
    store.save_section("business", data)
    return data


@focus_router.post("/jobs")
async def queue_job(request: Request, body: JobBody):
    return _store(request).add_job(body.model_dump())


@focus_router.post("/jobs/{job_id}/draft")
async def draft_job(request: Request, job_id: str):
    """Write a cover note for a queued job. The person submits it themselves."""
    store = _store(request)
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    resume = store.job_profile().get("resume") or ""
    if not resume:
        raise HTTPException(status_code=409, detail="Add your resume first")
    draft = await _draft_cover(request, job, resume)
    return store.update_job(job_id, draft=draft, status="drafted")


@focus_router.post("/jobs/{job_id}/status")
async def set_job_status(request: Request, job_id: str, body: JobStatusBody):
    job = _store(request).update_job(job_id, status=body.status)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _spanish_state(request: Request) -> dict:
    return _store(request).section("spanish") or spanish.new_state()


@focus_router.get("/spanish")
async def spanish_lesson(request: Request):
    state = _spanish_state(request)
    return {"card": spanish.next_card(state), "progress": spanish.progress(state)}


@focus_router.post("/spanish/answer")
async def spanish_answer(request: Request, body: SpanishAnswerBody):
    state = _spanish_state(request)
    try:
        result = spanish.answer(state, body.item_id, body.action, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _store(request).save_section("spanish", state)
    return {
        "result": result,
        "card": spanish.next_card(state),
        "progress": spanish.progress(state),
    }


_MAX_AUDIO_BYTES = 8 * 1024 * 1024


def _stt(request: Request):
    """Speech recognition, created on first use so it never slows startup."""
    app = request.app
    backend = getattr(app.state, "speech_backend", None)
    backend = backend or getattr(app.state, "focus_stt", None)
    if backend is None:
        from openjarvis.speech.faster_whisper import FasterWhisperBackend

        backend = FasterWhisperBackend(
            model_size=os.environ.get("OPENJARVIS_FOCUS_STT_MODEL", "base"),
            device="auto",
            compute_type="int8",
        )
        app.state.focus_stt = backend
    return backend


async def _read_audio(request: Request) -> tuple:
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="Missing 'file' field")
    audio = await upload.read()
    if not audio:
        raise HTTPException(status_code=400, detail="The recording was empty")
    if len(audio) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="The recording is too long")
    filename = getattr(upload, "filename", "") or "audio.wav"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "wav"
    if not re.fullmatch(r"[a-z0-9]{1,5}", ext):
        ext = "wav"
    return audio, ext


def _steered_transcribe(
    backend,
    audio: bytes,
    ext: str,
    language: Optional[str],
    hint: str,
    quick: bool = False,
) -> str:
    """Transcribe with settings suited to short phrases.

    Uses the Whisper model directly so a vocabulary hint can steer it and
    silence can be dropped. Any backend without that model, or any failure
    on this path, falls back to the backend's plain ``transcribe``.
    """
    ensure = getattr(backend, "_ensure_model", None)
    if ensure is not None:
        path = ""
        try:
            model = ensure()
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(audio)
                path = tmp.name
            # ``quick`` trades a little accuracy for speed: used for commands,
            # where the wake word and a retry make a slip cheap.
            options = {
                "beam_size": 1 if quick else 5,
                "condition_on_previous_text": False,
                "without_timestamps": True,
            }
            if language:
                options["language"] = language
            if hint:
                options["initial_prompt"] = hint
            try:
                segments, _info = model.transcribe(path, vad_filter=True, **options)
                segments = list(segments)
            except Exception:
                logger.debug("Voice filter unavailable; transcribing without it")
                segments, _info = model.transcribe(path, **options)
                segments = list(segments)
            kept = [s.text for s in segments if getattr(s, "no_speech_prob", 0) < 0.8]
            return "".join(kept).strip()
        except Exception:
            logger.exception("Steered transcription failed; using the plain path")
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    result = backend.transcribe(audio, format=ext, language=language)
    return (result.text or "").strip()


async def _run_stt(
    request: Request,
    audio: bytes,
    ext: str,
    language: Optional[str],
    hint: str = "",
    quick: bool = False,
) -> str:
    try:
        backend = _stt(request)
        return await asyncio.to_thread(
            _steered_transcribe, backend, audio, ext, language, hint, quick
        )
    except Exception as exc:
        logger.exception("Focus transcription failed")
        raise HTTPException(
            status_code=503, detail="Speech recognition is not available"
        ) from exc


async def _transcribe(
    request: Request, language: Optional[str], hint: str = "", quick: bool = False
) -> str:
    audio, ext = await _read_audio(request)
    return await _run_stt(request, audio, ext, language, hint, quick)


@focus_router.post("/listen/warm")
async def warm_listening(request: Request):
    """Start loading speech recognition in the background; report readiness."""
    state = request.app.state
    if getattr(state, "focus_stt_ready", False):
        return {"ready": True}
    task = getattr(state, "focus_stt_task", None)
    if task is None or task.done():
        try:
            backend = _stt(request)
        except Exception:
            logger.exception("Speech recognition could not be created")
            return {"ready": False, "error": "Speech recognition is not installed"}
        health = getattr(backend, "health", None)
        if health is None:
            state.focus_stt_ready = True
            return {"ready": True}

        async def _load() -> None:
            state.focus_stt_ready = bool(await asyncio.to_thread(health))

        state.focus_stt_task = asyncio.create_task(_load())
    return {"ready": False}


# Steers speech recognition toward the words commands are made of.
_COMMAND_HINT = (
    "Hey Morpheus, add this to my to-do list. Hey Morpheus, remind me tomorrow. "
    "Hey Morpheus, add it to my brain dump. Hey Morpheus, teach me Spanish."
)
_LESSON_COMMAND_HINT = "Hey Morpheus. Skip. Stop. Say it again. I know this."


@focus_router.post("/listen")
async def listen(request: Request):
    """Turn a short recording into text. The audio is not kept."""
    form = await request.form()
    language = form.get("language")
    language = language if isinstance(language, str) and language else None
    hint = _COMMAND_HINT if language == "en" else ""
    return {"text": await _transcribe(request, language, hint, quick=True)}


@focus_router.post("/spanish/say")
async def spanish_say(request: Request):
    """Check a spoken attempt at the current card against what was heard."""
    form = await request.form()
    item_id = form.get("item_id")
    if not isinstance(item_id, str) or item_id not in spanish._ITEMS:
        raise HTTPException(status_code=400, detail="Unknown item")
    hint = ", ".join(spanish.unit_vocabulary(item_id))
    heard = await _transcribe(request, "es", hint)
    state = _spanish_state(request)
    correct = spanish.heard_matches(item_id, heard)
    if correct:
        spanish.answer(state, item_id, "spoken")
        _store(request).save_section("spanish", state)
    item = spanish._ITEMS[item_id]
    return {
        "heard": heard,
        "correct": correct,
        "es": item["es"],
        "en": item["en"],
        "card": spanish.next_card(state),
        "progress": spanish.progress(state),
    }


@focus_router.post("/spanish/voice")
async def spanish_voice(request: Request):
    """One spoken answer in a voice lesson, or a spoken lesson command.

    A miss changes nothing unless ``final`` is set, so the learner gets a
    second try before a slip (or a recognition error) costs any progress.
    """
    form = await request.form()
    item_id = form.get("item_id")
    kind = form.get("kind")
    if not isinstance(item_id, str) or item_id not in spanish._ITEMS:
        raise HTTPException(status_code=400, detail="Unknown item")
    if kind not in ("intro", "choose", "type"):
        raise HTTPException(status_code=400, detail="Unknown card kind")
    final = form.get("final") == "1"
    audio, ext = await _read_audio(request)
    item = spanish._ITEMS[item_id]
    hint = ", ".join(spanish.unit_vocabulary(item_id))
    heard = await _run_stt(request, audio, ext, "es", hint)
    correct = spanish.check_voice(item_id, heard)
    command = None
    wake_text = ""
    if not correct:
        command = spanish.spoken_command(heard)
        if command is None:
            # English said during a Spanish answer ("skip", "hey Jarvis...")
            # is garbled by Spanish recognition, so listen again in English.
            english = await _run_stt(request, audio, ext, "en", _LESSON_COMMAND_HINT)
            command = spanish.spoken_command(english)
            if commands.extract_command(english) is not None:
                command, wake_text = "wake", english
        if command is None and (not heard or spanish.is_ghost(heard)):
            # Nothing usable was heard; do not count it as an attempt.
            command = "unclear"
    state = _spanish_state(request)
    if command == "skip":
        spanish.answer(state, item_id, "skip")
    elif command is None and correct:
        if kind == "intro":
            spanish.answer(state, item_id, "spoken")
        else:
            right = item["en"] if kind == "choose" else item["es"]
            spanish.answer(state, item_id, "answer", right)
    elif command is None and final:
        spanish.answer(state, item_id, "seen" if kind == "intro" else "answer", "")
    else:
        spanish.touch(state)
    _store(request).save_section("spanish", state)
    return {
        "heard": heard,
        "correct": correct and command is None,
        "command": command,
        "wake_text": wake_text,
        "es": item["es"],
        "en": item["en"],
        "card": spanish.next_card(state),
        "progress": spanish.progress(state),
    }


_TUTOR_PROMPT = (
    "You are a warm, patient Spanish tutor for an absolute beginner who lives "
    "in Mexico. The learner talks to you by voice, so their text is a speech "
    "recognition transcript and may contain recognition mistakes.\n"
    "Reply with only a JSON object with three string fields:\n"
    '"say": your reply in very simple Mexican Spanish, at most 12 words, '
    "ending with an easy question that keeps the conversation going.\n"
    '"meaning": the English translation of "say".\n'
    '"correction": one short English sentence about the learner\'s last '
    "message: if they made a mistake, give the right way to say it in "
    "Spanish; if they spoke English, give the Spanish for what they said; if "
    "it was fine, an empty string.\n"
    "Prefer words the learner already knows: {known}.\n\n"
    "Conversation so far:\n{history}\n\nLearner: {text}"
)


def parse_tutor(text: str) -> dict:
    """Pull the tutor's three fields out of a model reply."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and str(data.get("say", "")).strip():
                return {
                    key: str(data.get(key, "")).strip()
                    for key in ("say", "meaning", "correction")
                }
        except ValueError:
            pass
    return {"say": (text or "").strip()[:300], "meaning": "", "correction": ""}


@focus_router.post("/spanish/converse")
async def spanish_converse(request: Request, body: ConverseBody):
    """One turn of spoken Spanish practice: a reply plus a correction."""
    state = _spanish_state(request)
    known = ", ".join(spanish.known_phrases(state)) or "hola, gracias"
    history = "\n".join(
        f"{'Learner' if turn.role == 'you' else 'Tutor'}: {turn.text}"
        for turn in body.history
    )
    prompt = _TUTOR_PROMPT.format(
        known=known,
        history=history or "(nothing yet)",
        text=body.text.strip() or "(The learner has just sat down. Greet them.)",
    )
    try:
        reply = await _complete(request, prompt, temperature=0.5, max_tokens=300)
    except NoModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Tutor reply failed")
        raise HTTPException(status_code=502, detail="The model call failed") from exc
    spanish.touch(state)
    _store(request).save_section("spanish", state)
    return {**parse_tutor(reply), "progress": spanish.progress(state)}


_INTENT_PROMPT = (
    "A person said this to their voice assistant: {text!r}\n\n"
    "Decide what they want and reply with only a JSON object:\n"
    '{{"intent": one of "task", "note", "idea", "goal", "sprint", "step_done", '
    '"spanish", "stop", "email", "answer", "none", '
    '"text": string, "when": string, "amount": number}}\n'
    "- task: something to do or an appointment. text = a short title in their "
    "words; when = the date and time words copied exactly from what they said "
    '(for example "tomorrow at 3 pm"), or "" if none.\n'
    "- note: a thought to keep. idea: a business or project idea. text = it.\n"
    "- goal: money made or lost toward a savings goal. amount = dollars, "
    "negative for a loss; text = a short note.\n"
    "- sprint: start a focus timer. step_done: they finished the current step. "
    "spanish: start a Spanish lesson. stop: stop listening.\n"
    "- email: anything about their inbox.\n"
    "- answer: a question. text = your answer in at most two short spoken "
    "sentences. You cannot take actions, so never say you did or will.\n"
    "- none: unclear."
)


async def _model_plan(request: Request, command: str) -> Optional[dict]:
    """Ask the online model what a request means. None when it cannot help."""
    api_key = _api_key()
    if not api_key:
        return None
    try:
        raw = await _claude(api_key, _INTENT_PROMPT.format(text=command))
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else None
    except Exception:
        logger.exception("Online model could not read the request")
        return None
    if not isinstance(data, dict):
        return None
    plan = commands.from_model(data)
    return None if plan["intent"] == "none" else plan


_ANSWER_PROMPT = (
    "You are Morpheus, a calm voice assistant. Answer the question below in at "
    "most two short sentences of plain spoken English, with no lists or "
    "symbols. You cannot take actions, set reminders or change anything, so "
    "never say that you did or will.\n\nQuestion: {text}"
)


async def _improve_steps(request: Request, task_id: str, title: str) -> None:
    """Break a voice-added to-do into steps after Jarvis has already replied."""
    steps = await _breakdown(request, title)
    if steps != [title]:
        _store(request).set_steps(task_id, steps)


def _add_spoken_task(request: Request, plan: dict) -> str:
    task = _store(request).add_task(plan["title"], [plan["title"]], plan["due"])
    _copy_to_calendar(request, plan["title"], plan["due"])
    job = asyncio.create_task(_improve_steps(request, task["id"], plan["title"]))
    jobs = getattr(request.app.state, "focus_jobs", None)
    if jobs is None:
        jobs = request.app.state.focus_jobs = set()
    jobs.add(job)
    job.add_done_callback(jobs.discard)
    when = f" for {plan['when']}" if plan["when"] else ""
    return f"Added {plan['title']}{when}."


async def _mail_summary(request: Request) -> str:
    data = await unread_mail(request)
    for _ in range(40):
        if not data["loading"]:
            break
        await asyncio.sleep(0.5)
        data = await unread_mail(request)
    if not data["available"]:
        return "I can only read mail on your Mac."
    if data["loading"]:
        return "Your mail is still loading. Ask me again in a minute."
    if data["error"]:
        return "I could not read your mail. " + data["error"]
    if not data["unread"]:
        return "You have no unread mail."
    first = data["latest"][0] if data["latest"] else None
    line = f"You have {data['unread']} unread."
    if first:
        sender = re.sub(r"\s*<.*>", "", first.get("from", "")).strip() or "someone"
        line += f" The newest is from {sender}: {first.get('subject', '')}."
    return line


@focus_router.post("/command")
async def voice_command(request: Request, body: CommandBody):
    """Carry out one spoken command. Ignored unless the wake word was said."""
    heard = body.text.strip()
    command = commands.extract_command(heard)
    if command is None:
        if not (body.awake or body.pending):
            return {"wake": False, "heard": heard}
        command = heard
    out = {"wake": True, "heard": heard, "action": None, "pending": ""}
    if not command:
        return {**out, "intent": "none", "say": "Yes?", "pending": body.pending}
    store = _store(request)
    if body.pending:
        plan = commands.interpret_pending(body.pending, command)
    else:
        plan = commands.interpret(command)
        if plan["intent"] in ("confirm", "question"):
            # The fixed rules were unsure; let the online model read it.
            plan = await _model_plan(request, command) or plan
    intent = out["intent"] = plan["intent"]
    if intent == "task":
        out["say"] = _add_spoken_task(request, plan)
    elif intent == "ask":
        out["pending"] = plan["pending"]
        out["say"] = "What should I add?"
    elif intent == "confirm":
        out["pending"] = f"confirm:{plan['title']}"
        out["say"] = f"Should I add {plan['title']} to your to-dos?"
    elif intent == "cancel":
        out["say"] = "Okay, never mind."
    elif intent == "capture":
        store.add_capture(plan["text"], kind=plan["kind"])
        place = "idea list" if plan["kind"] == "idea" else "brain dump"
        out["say"] = f"Added to your {place}: {plan['text']}."
    elif intent == "goal":
        store.add_goal_entry(plan["amount"], plan["note"])
        saved = store.snapshot()["goal"]["saved"]
        verb = "Added" if plan["amount"] > 0 else "Took off"
        amount = abs(plan["amount"])
        out["say"] = f"{verb} {amount:g} dollars. Your goal is at {saved:g} dollars."
    elif intent == "action":
        out["action"] = plan["action"]
        out["say"] = {
            "sprint": "Sprint started. Just this one step.",
            "step_done": "",
            "spanish": "",
            "stop": "Okay. I will stop listening.",
        }[plan["action"]]
    elif intent == "answer":
        out["say"] = plan["text"]
    elif intent == "email":
        out["say"] = await _mail_summary(request)
    else:
        try:
            out["say"] = await _complete(
                request,
                _ANSWER_PROMPT.format(text=command),
                temperature=0.3,
                max_tokens=120,
            )
        except Exception:
            logger.exception("Spoken question could not be answered")
            out["say"] = ""
        out["say"] = out["say"] or "I do not have an answer for that."
    return out


def _alert_allowed(request: Request, token: Optional[str]) -> bool:
    """Chart alerts need the shared token, or must come from this machine."""
    expected = os.environ.get("OPENJARVIS_FOCUS_ALERT_TOKEN", "")
    if expected:
        return hmac.compare_digest(expected, token or "")
    host = request.client.host if request.client else ""
    return host in _LOOPBACK


@focus_router.post("/alert")
async def chart_alert(request: Request, body: AlertBody):
    """Receive an entry or exit alert from the trader's own chart.

    Advisory only: the reply says what to do; no order is placed.
    """
    if not _alert_allowed(request, body.token):
        raise HTTPException(status_code=403, detail="Alert token missing or wrong")
    store = _store(request)
    try:
        symbol = normalize_symbol(body.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    side = body.side.lower()
    if side == "exit":
        signal = {
            "symbol": symbol,
            "side": "exit",
            "action": "exit",
            "price": body.price,
            "say": f"{symbol}: get out now, near {body.price:g}.",
        }
    elif side in ("long", "short"):
        if body.atr is None:
            raise HTTPException(status_code=400, detail="Entry alerts need 'atr'")
        risk = float(store.snapshot()["risk_per_trade"])
        signal = plan_trade(symbol, side, body.price, body.atr, risk)
    else:
        raise HTTPException(status_code=400, detail="side must be long, short or exit")
    return store.add_signal(signal)


@focus_router.post("/speak")
async def speak(request: Request, body: SpeakBody):
    """Speak ``text`` in the calm or the deadline voice. Returns WAV audio."""
    from openjarvis.server.api_routes import _resolve_tts_backend

    backend = await _resolve_tts_backend(request)
    if backend is None:
        raise HTTPException(
            status_code=501, detail="No text-to-speech backend available"
        )
    voice_id, speed = VOICE_MODES.get(body.mode, VOICE_MODES["calm"])
    if body.mode == "calm" and body.voice in CALM_VOICES:
        voice_id = body.voice
    if getattr(backend, "backend_id", "kokoro") != "kokoro":
        voice_id = ""
    text = body.text.strip()
    # Lines are often repeated ("Correct.", "What should I add?"), and making
    # speech is the slow part, so keep what has already been generated.
    cache = getattr(request.app.state, "focus_speech_cache", None)
    if cache is None:
        cache = request.app.state.focus_speech_cache = {}
    key = (voice_id, speed, text)
    audio = cache.get(key)
    if audio is None:
        try:
            result = await asyncio.to_thread(
                backend.synthesize,
                text,
                voice_id=voice_id,
                speed=speed,
                output_format="wav",
            )
        except Exception as exc:
            logger.exception("Focus speech failed")
            raise HTTPException(
                status_code=500, detail="Speech synthesis failed"
            ) from exc
        audio = result.audio
        if len(cache) >= 300:
            cache.clear()
        cache[key] = audio
    return Response(content=audio, media_type="audio/wav")
