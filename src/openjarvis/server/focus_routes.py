"""API for the Focus HUD: tasks broken into steps, quick capture, voice modes."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from openjarvis.focus.signals import (
    CONTRACTS,
    max_stop_points,
    normalize_symbol,
    plan_trade,
)
from openjarvis.focus.store import FocusStore

logger = logging.getLogger(__name__)

focus_router = APIRouter(prefix="/v1/focus", tags=["focus"])

# Two original Kokoro voices: a slow, low one by default and a brisk, firm
# one once a deadline has passed.
VOICE_MODES = {
    "calm": ("bm_george", 0.88),
    "deadline": ("am_eric", 1.1),
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


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    mode: str = "calm"


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
    return [ln for ln in lines if ln][:8]


def _breakdown(request: Request, title: str) -> List[str]:
    engine = getattr(request.app.state, "engine", None)
    model = getattr(request.app.state, "model", None)
    if engine is None or not model:
        return [title]
    from openjarvis.core.types import Message, Role

    try:
        result = engine.generate(
            [Message(role=Role.USER, content=_BREAKDOWN_PROMPT.format(title=title))],
            model=model,
            temperature=0.2,
            max_tokens=300,
        )
        return parse_steps(result.get("content") or "") or [title]
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
        steps = await asyncio.to_thread(_breakdown, request, title)
    return _store(request).add_task(title, steps, body.due)


@focus_router.post("/task/{task_id}/step")
async def step_done(request: Request, task_id: str):
    task = _store(request).advance(task_id)
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
    return _store(request).add_capture(body.text.strip())


@focus_router.delete("/capture/{capture_id}")
async def delete_capture(request: Request, capture_id: str):
    if not _store(request).remove_capture(capture_id):
        raise HTTPException(status_code=404, detail="Capture not found")
    return {"ok": True}


@focus_router.put("/risk")
async def set_risk(request: Request, body: RiskBody):
    return {"risk_per_trade": _store(request).set_risk(body.risk_per_trade)}


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


def _draft_cover(request: Request, job: dict, resume: str) -> str:
    engine = getattr(request.app.state, "engine", None)
    model = getattr(request.app.state, "model", None)
    if engine is None or not model:
        raise HTTPException(status_code=503, detail="No model is configured")
    from openjarvis.core.types import Message, Role

    prompt = _COVER_PROMPT.format(
        title=job["title"],
        company=job.get("company") or "the company",
        description=(job.get("description") or "")[:6000],
        resume=resume[:12000],
    )
    try:
        result = engine.generate(
            [Message(role=Role.USER, content=prompt)],
            model=model,
            temperature=0.4,
            max_tokens=400,
        )
    except Exception as exc:
        logger.exception("Cover note draft failed")
        raise HTTPException(status_code=502, detail="The model call failed") from exc
    text = (result.get("content") or "").strip()
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
    draft = await asyncio.to_thread(_draft_cover, request, job, resume)
    return store.update_job(job_id, draft=draft, status="drafted")


@focus_router.post("/jobs/{job_id}/status")
async def set_job_status(request: Request, job_id: str, body: JobStatusBody):
    job = _store(request).update_job(job_id, status=body.status)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


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
    if getattr(backend, "backend_id", "kokoro") != "kokoro":
        voice_id = ""
    try:
        result = await asyncio.to_thread(
            backend.synthesize,
            body.text.strip(),
            voice_id=voice_id,
            speed=speed,
            output_format="wav",
        )
    except Exception as exc:
        logger.exception("Focus speech failed")
        raise HTTPException(status_code=500, detail="Speech synthesis failed") from exc
    return Response(content=result.audio, media_type="audio/wav")
