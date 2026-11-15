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

from openjarvis.focus import commands, spanish
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


# Online model used when an Anthropic key is saved in the app's Cloud Models
# settings. Without a key the local model answers instead.
CLOUD_MODEL = os.environ.get("OPENJARVIS_FOCUS_MODEL", "claude-haiku-5-5")


class NoModelError(RuntimeError):
    """Neither an online key nor a local model is available."""


async def _complete(
    request: Request, prompt: str, *, temperature: float, max_tokens: int
) -> str:
    """One model reply: the online model when a key is saved, else local."""
    from openjarvis.core.types import Message, Role
    from openjarvis.server import cloud_router

    messages = [Message(role=Role.USER, content=prompt)]
    if cloud_router._load_keys().get("ANTHROPIC_API_KEY"):
        try:
            parts = []
            async for token in cloud_router.stream_cloud(
                CLOUD_MODEL, messages, temperature=temperature, max_tokens=max_tokens
            ):
                parts.append(token)
            text = "".join(parts).strip()
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
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (result.get("content") or "").strip()


async def _breakdown(request: Request, title: str) -> List[str]:
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
    return _store(request).add_capture(body.text.strip(), kind=body.kind)


@focus_router.delete("/capture/{capture_id}")
async def delete_capture(request: Request, capture_id: str):
    if not _store(request).remove_capture(capture_id):
        raise HTTPException(status_code=404, detail="Capture not found")
    return {"ok": True}


@focus_router.put("/risk")
async def set_risk(request: Request, body: RiskBody):
    return {"risk_per_trade": _store(request).set_risk(body.risk_per_trade)}


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


async def _transcribe(request: Request, language: Optional[str]) -> str:
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
    try:
        backend = _stt(request)
        result = await asyncio.to_thread(
            backend.transcribe, audio, format=ext, language=language
        )
    except Exception as exc:
        logger.exception("Focus transcription failed")
        raise HTTPException(
            status_code=503, detail="Speech recognition is not available"
        ) from exc
    return (result.text or "").strip()


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


@focus_router.post("/listen")
async def listen(request: Request):
    """Turn a short recording into text. The audio is not kept."""
    form = await request.form()
    language = form.get("language")
    language = language if isinstance(language, str) and language else None
    return {"text": await _transcribe(request, language)}


@focus_router.post("/spanish/say")
async def spanish_say(request: Request):
    """Check a spoken attempt at the current card against what was heard."""
    form = await request.form()
    item_id = form.get("item_id")
    if not isinstance(item_id, str) or item_id not in spanish._ITEMS:
        raise HTTPException(status_code=400, detail="Unknown item")
    heard = await _transcribe(request, "es")
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


_ANSWER_PROMPT = (
    "You are Jarvis, a calm voice assistant. Answer the question below in at "
    "most two short sentences of plain spoken English, with no lists or "
    "symbols.\n\nQuestion: {text}"
)


@focus_router.post("/command")
async def voice_command(request: Request, body: CommandBody):
    """Carry out one spoken command. Ignored unless the wake word was said."""
    heard = body.text.strip()
    command = commands.extract_command(heard)
    if command is None:
        if not body.awake:
            return {"wake": False, "heard": heard}
        command = heard
    if not command:
        return {"wake": True, "heard": heard, "intent": "none", "say": "Yes?"}
    store = _store(request)
    plan = commands.interpret(command)
    intent = plan["intent"]
    out = {"wake": True, "heard": heard, "intent": intent, "action": None}
    if intent == "task":
        steps = await _breakdown(request, plan["title"])
        store.add_task(plan["title"], steps, plan["due"])
        when = f" for {plan['when']}" if plan["when"] else ""
        out["say"] = f"Added {plan['title']}{when}."
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
    elif intent == "email":
        out["say"] = "Your email is not connected yet, so I cannot do that."
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
        out["say"] = out["say"] or "I heard you, but I do not know how to do that yet."
    return out


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
