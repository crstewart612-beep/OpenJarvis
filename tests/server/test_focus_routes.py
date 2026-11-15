from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.focus.store import FocusStore  # noqa: E402
from openjarvis.server import cloud_router as _cloud_router  # noqa: E402
from openjarvis.server.focus_routes import focus_router, parse_steps  # noqa: E402

_REAL_LOAD_KEYS = _cloud_router._load_keys


class _Engine:
    def generate(self, messages, *, model, **kwargs):
        return {"content": 'Sure:\n["Open mail", "Attach the PDF", "Press send"]'}


@pytest.fixture(autouse=True)
def _no_cloud_keys(monkeypatch):
    from openjarvis.server import cloud_router

    monkeypatch.setattr(cloud_router, "_load_keys", lambda: {})


class _Heard:
    def __init__(self, text):
        self.text = text


class _Stt:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def transcribe(self, audio, *, format="wav", language=None):
        self.calls.append((len(audio), format, language))
        return _Heard(self._text)


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(focus_router)
    app.state.focus_store = FocusStore(tmp_path / "focus.json")
    app.state.engine = _Engine()
    app.state.model = "test"
    app.state.focus_local_steps = True
    return TestClient(app)


def test_parse_steps_handles_json_and_lists():
    assert parse_steps('x ["a", "b"] y') == ["a", "b"]
    assert parse_steps("1. Open mail\n- Press send") == ["Open mail", "Press send"]


def test_task_is_broken_down_and_stepped(client):
    task = client.post("/v1/focus/task", json={"title": "Send invoice"}).json()
    assert task["steps"] == ["Open mail", "Attach the PDF", "Press send"]
    state = client.get("/v1/focus/state").json()
    assert state["current"]["id"] == task["id"]
    assert state["mode"] == "calm"
    for _ in range(3):
        task = client.post(f"/v1/focus/task/{task['id']}/step").json()
    assert task["done"] is True
    assert client.get("/v1/focus/state").json()["current"] is None


def test_overdue_task_switches_to_deadline_mode(client):
    past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="minutes")
    client.post("/v1/focus/task", json={"title": "Later", "steps": ["a"]})
    late = client.post(
        "/v1/focus/task", json={"title": "Late", "steps": ["a"], "due": past}
    ).json()
    state = client.get("/v1/focus/state").json()
    assert state["mode"] == "deadline"
    assert state["current"]["id"] == late["id"]


def test_capture_add_and_remove(client):
    item = client.post("/v1/focus/capture", json={"text": "call dentist"}).json()
    assert client.get("/v1/focus/state").json()["captures"][0]["text"] == "call dentist"
    assert client.delete(f"/v1/focus/capture/{item['id']}").status_code == 200
    assert client.delete(f"/v1/focus/capture/{item['id']}").status_code == 404


def test_sizing_uses_risk_limit(client):
    data = client.get("/v1/focus/sizing").json()
    assert data["risk_per_trade"] == 20
    stops = {c["symbol"]: c["max_stop_points"] for c in data["contracts"]}
    assert stops == {"MES": 4.0, "MNQ": 10.0, "MYM": 40, "M2K": 4.0}
    client.put("/v1/focus/risk", json={"risk_per_trade": 25})
    mes = client.get("/v1/focus/sizing").json()["contracts"][0]
    assert mes["max_stop_points"] == 5.0


def test_chart_alert_becomes_sized_signal(client):
    body = {"symbol": "MES1!", "side": "long", "price": 5000, "atr": 2}
    sig = client.post("/v1/focus/alert", json=body).json()
    assert sig["action"] == "long" and sig["contracts"] == 1 and sig["stop"] == 4997
    out = client.post(
        "/v1/focus/alert", json={"symbol": "MES", "side": "exit", "price": 5004}
    ).json()
    assert out["action"] == "exit"
    signals = client.get("/v1/focus/state").json()["signals"]
    assert [s["action"] for s in signals] == ["exit"]


def test_chart_alert_validation_and_token(client, monkeypatch):
    bad = {"symbol": "NES", "side": "long", "price": 1, "atr": 1}
    assert client.post("/v1/focus/alert", json=bad).status_code == 400
    no_atr = {"symbol": "MES", "side": "long", "price": 1}
    assert client.post("/v1/focus/alert", json=no_atr).status_code == 400
    monkeypatch.setenv("OPENJARVIS_FOCUS_ALERT_TOKEN", "s3cret")
    ok = {"symbol": "MES", "side": "exit", "price": 5000}
    assert client.post("/v1/focus/alert", json=ok).status_code == 403
    ok["token"] = "s3cret"
    assert client.post("/v1/focus/alert", json=ok).status_code == 200


def test_job_queue_draft_and_submit(client):
    job = client.post(
        "/v1/focus/jobs",
        json={
            "title": "Process Improvement Manager",
            "company": "Acme",
            "url": "https://example.com/job/1",
            "salary": "$90k",
        },
    ).json()
    assert job["status"] == "found"
    again = client.post(
        "/v1/focus/jobs", json={"title": "Dup", "url": "https://example.com/job/1"}
    ).json()
    assert again["id"] == job["id"]
    assert client.post(f"/v1/focus/jobs/{job['id']}/draft").status_code == 409
    prof = client.put(
        "/v1/focus/jobs/profile",
        json={"titles": ["Change Management"], "min_salary": 65000, "resume": "CV"},
    ).json()
    assert prof == {
        "titles": ["Change Management"],
        "min_salary": 65000,
        "has_resume": True,
    }
    drafted = client.post(f"/v1/focus/jobs/{job['id']}/draft").json()
    assert drafted["status"] == "drafted" and drafted["draft"]
    state = client.get("/v1/focus/state").json()
    assert "resume" not in state["job_profile"]
    assert state["job_profile"]["has_resume"] is True
    sent = client.post(
        f"/v1/focus/jobs/{job['id']}/status", json={"status": "submitted"}
    ).json()
    assert sent["status"] == "submitted"
    bad = client.post(f"/v1/focus/jobs/{job['id']}/status", json={"status": "x"})
    assert bad.status_code == 422


def test_job_link_must_be_http(client):
    r = client.post("/v1/focus/jobs", json={"title": "T", "url": "javascript:alert(1)"})
    assert r.status_code == 422


def test_spanish_lesson_flow(client):
    first = client.get("/v1/focus/spanish").json()
    assert first["card"]["kind"] == "intro"
    assert first["progress"]["unit"] == "Greetings"
    item = first["card"]["item_id"]
    out = client.post(
        "/v1/focus/spanish/answer", json={"item_id": item, "action": "skip_unit"}
    ).json()
    assert out["progress"]["unit"] == "Numbers 1 to 10"
    assert client.get("/v1/focus/spanish").json()["progress"]["words_mastered"] == 12
    assert "sections" not in client.get("/v1/focus/state").json()
    bad = client.post(
        "/v1/focus/spanish/answer", json={"item_id": "nope:1", "action": "seen"}
    )
    assert bad.status_code == 400


def test_listen_returns_text_and_validates(client):
    client.app.state.focus_stt = _Stt(" hello there ")
    r = client.post(
        "/v1/focus/listen", files={"file": ("a.webm", b"abc", "audio/webm")}
    )
    assert r.json() == {"text": "hello there"}
    assert client.app.state.focus_stt.calls == [(3, "webm", None)]
    assert client.post("/v1/focus/listen", data={"x": "1"}).status_code == 400
    empty = client.post(
        "/v1/focus/listen", files={"file": ("a.webm", b"", "audio/webm")}
    )
    assert empty.status_code == 400


def test_spanish_say_marks_progress_only_when_heard(client):
    client.app.state.focus_stt = _Stt("Hola.")
    files = {"file": ("a.mp4", b"abc", "audio/mp4")}
    ok = client.post(
        "/v1/focus/spanish/say", files=files, data={"item_id": "greetings:0"}
    ).json()
    assert ok["correct"] is True and ok["heard"] == "Hola."
    assert client.app.state.focus_stt.calls[0][2] == "es"
    client.app.state.focus_stt = _Stt("ola k ase")
    miss = client.post(
        "/v1/focus/spanish/say", files=files, data={"item_id": "greetings:1"}
    ).json()
    assert miss["correct"] is False and miss["es"] == "buenos días"
    bad = client.post("/v1/focus/spanish/say", files=files, data={"item_id": "x"})
    assert bad.status_code == 400


def test_spanish_converse_parses_tutor_reply(client):
    class Tutor:
        def __init__(self):
            self.prompts = []

        def generate(self, messages, *, model, **kwargs):
            self.prompts.append(messages[0].content)
            return {
                "content": 'ok {"say": "¡Hola! ¿Cómo estás?", "meaning": '
                '"Hello! How are you?", "correction": "Say: buenos días."}'
            }

    tutor = Tutor()
    client.app.state.engine = tutor
    out = client.post(
        "/v1/focus/spanish/converse",
        json={"text": "bueno dia", "history": [{"role": "tutor", "text": "Hola"}]},
    ).json()
    assert out["say"] == "¡Hola! ¿Cómo estás?"
    assert out["meaning"] == "Hello! How are you?"
    assert out["correction"] == "Say: buenos días."
    assert "Learner: bueno dia" in tutor.prompts[0]
    assert "Tutor: Hola" in tutor.prompts[0]


def test_parse_tutor_falls_back_to_plain_text():
    from openjarvis.server.focus_routes import parse_tutor

    assert parse_tutor("Hola, ¿qué tal?") == {
        "say": "Hola, ¿qué tal?",
        "meaning": "",
        "correction": "",
    }


def test_online_model_is_used_when_a_key_is_saved(client, monkeypatch):
    from openjarvis.server import cloud_router, focus_routes

    seen = {}

    async def fake_claude(api_key, prompt):
        seen["key"] = api_key
        return '["Open mail"]'

    monkeypatch.setattr(cloud_router, "_load_keys", lambda: {"ANTHROPIC_API_KEY": "k"})
    monkeypatch.setattr(focus_routes, "_claude", fake_claude)
    task = client.post("/v1/focus/task", json={"title": "Send invoice"}).json()
    assert task["steps"] == ["Open mail"]
    assert seen["key"] == "k"
    assert focus_routes.CLOUD_MODEL.startswith("claude-")


def test_warm_listening_reports_ready(client):
    class Slow(_Stt):
        def health(self):
            return True

    client.app.state.focus_stt = Slow("x")
    first = client.post("/v1/focus/listen/warm").json()
    assert first == {"ready": False}
    for _ in range(50):
        if client.post("/v1/focus/listen/warm").json()["ready"]:
            break
    assert client.post("/v1/focus/listen/warm").json() == {"ready": True}


def test_goal_tracks_trade_results(client):
    goal = client.get("/v1/focus/state").json()["goal"]
    assert goal["target"] == 10000 and goal["saved"] == 0
    win = client.post(
        "/v1/focus/goal/entry", json={"amount": 45.5, "note": "MES long"}
    ).json()
    client.post("/v1/focus/goal/entry", json={"amount": -20})
    goal = client.get("/v1/focus/state").json()["goal"]
    assert goal["saved"] == 25.5
    assert [e["amount"] for e in goal["entries"]] == [-20, 45.5]
    client.put("/v1/focus/goal", json={"target": 12000, "name": "New car"})
    goal = client.get("/v1/focus/state").json()["goal"]
    assert goal["target"] == 12000 and goal["name"] == "New car"
    assert client.delete(f"/v1/focus/goal/entry/{win['id']}").status_code == 200
    assert client.get("/v1/focus/state").json()["goal"]["saved"] == -20
    assert client.post("/v1/focus/goal/entry", json={"amount": 0}).status_code == 400
    assert client.put("/v1/focus/goal", json={"target": -5}).status_code == 422


def test_voice_command_needs_the_wake_word(client):
    out = client.post("/v1/focus/command", json={"text": "add milk to my list"}).json()
    assert out == {"wake": False, "heard": "add milk to my list"}
    assert client.get("/v1/focus/state").json()["tasks"] == []
    only = client.post("/v1/focus/command", json={"text": "Hey Jarvis."}).json()
    assert only["say"] == "Yes?"
    follow = client.post(
        "/v1/focus/command", json={"text": "add milk to my list", "awake": True}
    ).json()
    assert follow["intent"] == "task" and follow["say"] == "Added Milk."


def test_voice_commands_change_the_right_lists(client):
    def say(text):
        return client.post("/v1/focus/command", json={"text": text}).json()

    out = say(
        "Hey Jarvis, add dentist appointment to my calendar for tomorrow at 3 PM."
    )
    assert out["say"] == "Added Dentist appointment for tomorrow at 3 PM."
    say("Hey Jarvis, add a candle pop-up to my idea list")
    say("Hey Jarvis, add find hair products to my brain dump")
    goal = say("Hey Jarvis, I made 60 dollars on MES")
    assert goal["say"] == "Added 60 dollars. Your goal is at 60 dollars."
    assert say("Hey Jarvis, start a sprint")["action"] == "sprint"
    assert "mail" in say("Hey Jarvis, group my emails")["say"].lower()
    state = client.get("/v1/focus/state").json()
    assert state["tasks"][0]["title"] == "Dentist appointment"
    assert state["tasks"][0]["due"].endswith("T15:00")
    kinds = {c["text"]: c["kind"] for c in state["captures"]}
    assert kinds == {"Candle pop-up": "idea", "Find hair products": "note"}
    assert state["goal"]["entries"][0]["note"] == "On mes"


def test_unknown_command_is_answered_by_the_model(client):
    class Answer:
        def generate(self, messages, *, model, **kwargs):
            return {"content": "Ninety-six."}

    client.app.state.engine = Answer()
    out = client.post(
        "/v1/focus/command", json={"text": "Jarvis, what is twelve times eight?"}
    ).json()
    assert out["intent"] == "question" and out["say"] == "Ninety-six."


def test_vague_command_asks_then_adds(client):
    ask = client.post(
        "/v1/focus/command", json={"text": "Hey Jarvis, add something to my to do list"}
    ).json()
    assert ask["say"] == "What should I add?" and ask["pending"] == "task"
    assert client.get("/v1/focus/state").json()["tasks"] == []
    done = client.post(
        "/v1/focus/command",
        json={"text": "Pick up laundry tomorrow.", "pending": "task"},
    ).json()
    assert done["say"] == "Added Pick up laundry for tomorrow."
    assert done["pending"] == ""
    tasks = client.get("/v1/focus/state").json()["tasks"]
    assert [t["title"] for t in tasks] == ["Pick up laundry"]


def test_statement_with_a_date_is_added_and_others_are_confirmed(client):
    out = client.post(
        "/v1/focus/command", json={"text": "Jarvis, I'm picking up laundry tomorrow"}
    ).json()
    assert out["say"] == "Added Picking up laundry for tomorrow."
    ask = client.post(
        "/v1/focus/command", json={"text": "Jarvis, call the bank"}
    ).json()
    assert ask["say"] == "Should I add Call the bank to your to-dos?"
    yes = client.post(
        "/v1/focus/command", json={"text": "yes", "pending": ask["pending"]}
    ).json()
    assert yes["say"] == "Added Call the bank."


def test_parse_steps_ignores_empty_output():
    assert parse_steps("[]") == []
    assert parse_steps("- []\n1. Open mail") == ["Open mail"]


def test_voice_lesson_gives_a_second_try(client):
    files = {"file": ("a.mp4", b"abc", "audio/mp4")}

    def answer(heard, **data):
        client.app.state.focus_stt = _Stt(heard)
        return client.post("/v1/focus/spanish/voice", files=files, data=data).json()

    def streak(item):
        state = client.app.state.focus_store.section("spanish")
        return state["items"][item]["streak"]

    ok = answer("Hola.", item_id="greetings:0", kind="intro")
    assert ok["correct"] is True and ok["command"] is None
    assert client.app.state.focus_stt.calls[0][2] == "es"
    # A "what does it mean" card is also answered by saying the Spanish.
    assert answer("hola", item_id="greetings:0", kind="choose")["correct"] is True
    assert streak("greetings:0") == 2
    miss = answer("adiós", item_id="greetings:0", kind="type")
    assert miss["correct"] is False and miss["command"] is None
    assert streak("greetings:0") == 2
    answer("adiós", item_id="greetings:0", kind="type", final="1")
    assert streak("greetings:0") == 0
    skip = answer("I know this", item_id="greetings:1", kind="intro")
    assert skip["command"] == "skip" and skip["correct"] is False
    assert streak("greetings:1") == 3
    assert answer("stop", item_id="greetings:2", kind="type")["command"] == "stop"
    bad = answer("x", item_id="greetings:2", kind="nope")
    assert bad == {"detail": "Unknown card kind"}


def test_voice_lesson_ignores_invented_phrases_and_hears_the_wake_word(client):
    files = {"file": ("a.mp4", b"abc", "audio/mp4")}

    def answer(heard, **data):
        client.app.state.focus_stt = _Stt(heard)
        return client.post("/v1/focus/spanish/voice", files=files, data=data).json()

    ghost = answer(
        "Thank you very much.", item_id="greetings:5", kind="choose", final="1"
    )
    assert ghost["command"] == "unclear" and ghost["correct"] is False
    assert client.app.state.focus_store.section("spanish")["items"] == {}
    right = answer("Bien, gracias.", item_id="greetings:5", kind="choose")
    assert right["correct"] is True
    wake = answer(
        "Hey Jarvis, add milk to my list.", item_id="greetings:0", kind="type"
    )
    assert wake["command"] == "wake"
    assert wake["wake_text"] == "Hey Jarvis, add milk to my list."


def test_steered_transcription_uses_the_model_and_falls_back():
    from openjarvis.server.focus_routes import _steered_transcribe

    class Segment:
        def __init__(self, text, no_speech_prob=0.0):
            self.text, self.no_speech_prob = text, no_speech_prob

    class Model:
        def __init__(self):
            self.calls = []

        def transcribe(self, path, **options):
            self.calls.append(options)
            return iter([Segment(" hola"), Segment(" ruido", 0.95)]), None

    class Backend(_Stt):
        def __init__(self, model):
            super().__init__("plain path")
            self.model = model

        def _ensure_model(self):
            if self.model is None:
                raise RuntimeError("no model")
            return self.model

    model = Model()
    text = _steered_transcribe(Backend(model), b"abc", "mp4", "es", "hola, adiós")
    assert text == "hola"
    assert model.calls[0]["initial_prompt"] == "hola, adiós"
    assert model.calls[0]["language"] == "es" and model.calls[0]["vad_filter"] is True
    assert model.calls[0]["beam_size"] == 5
    _steered_transcribe(Backend(model), b"abc", "mp4", "en", "", quick=True)
    assert model.calls[-1]["beam_size"] == 1
    assert _steered_transcribe(Backend(None), b"abc", "mp4", "es", "") == "plain path"


def test_task_can_be_ticked_off_and_reopened(client):
    task = client.post(
        "/v1/focus/task", json={"title": "T", "steps": ["a", "b"]}
    ).json()
    done = client.post(f"/v1/focus/task/{task['id']}/done", json={"done": True}).json()
    assert done["done"] is True and done["step"] == 2
    assert client.get("/v1/focus/state").json()["current"] is None
    back = client.post(f"/v1/focus/task/{task['id']}/done", json={"done": False}).json()
    assert back["done"] is False and back["step"] == 0
    assert client.post("/v1/focus/task/nope/done", json={}).status_code == 404


def test_no_step_breakdown_without_the_online_model(client):
    client.app.state.focus_local_steps = False
    task = client.post("/v1/focus/task", json={"title": "Send invoice"}).json()
    assert task["steps"] == ["Send invoice"]


def test_speak_uses_the_chosen_calm_voice_and_caches(client):
    class Tts:
        backend_id = "kokoro"

        def __init__(self):
            self.calls = []

        def synthesize(self, text, **kwargs):
            self.calls.append((text, kwargs["voice_id"]))

            class Result:
                audio = b"RIFFdata"

            return Result()

    tts = Tts()
    client.app.state.tts_resolved = True
    client.app.state.tts_backend = tts

    def speak(**body):
        return client.post("/v1/focus/speak", json=body)

    assert speak(text="Hello", mode="calm", voice="am_onyx").content == b"RIFFdata"
    speak(text="Hello", mode="calm", voice="am_onyx")
    speak(text="Hello", mode="calm", voice="not_a_voice")
    speak(text="Now.", mode="deadline", voice="am_onyx")
    speak(text="hola", mode="es", voice="am_onyx")
    assert tts.calls == [
        ("Hello", "am_onyx"),
        ("Hello", "bm_george"),
        ("Now.", "am_eric"),
        ("hola", "em_alex"),
    ]


def test_online_request_shape(monkeypatch):
    """The request sent to Claude: fallbacks on, low effort, no sampling params."""
    import asyncio
    import types

    anthropic = pytest.importorskip("anthropic")

    from openjarvis.server import focus_routes

    calls = {}

    class FakeMessages:
        async def create(self, **kwargs):
            calls.update(kwargs)
            block = types.SimpleNamespace(type="text", text=" ready ")
            thinking = types.SimpleNamespace(type="thinking", thinking="")
            return types.SimpleNamespace(
                stop_reason="end_turn", content=[thinking, block]
            )

    class FakeClient:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.beta = types.SimpleNamespace(messages=FakeMessages())
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)
    monkeypatch.setattr(focus_routes, "CLOUD_MODEL", "claude-opus-5-5")
    assert asyncio.run(focus_routes._claude("sk-ant-x", "hi")) == "ready"
    assert calls["client"]["api_key"] == "sk-ant-x"
    assert calls["model"] == "claude-opus-5-5"
    assert calls["fallbacks"] == "default"
    assert calls["betas"] == ["server-side-fallback-2026-07-01"]
    assert calls["output_config"] == {"effort": "low"}
    assert "temperature" not in calls and "thinking" not in calls


def test_key_is_saved_locally_and_never_returned(client, monkeypatch, tmp_path):
    pytest.importorskip("anthropic")
    from openjarvis.server import cloud_router, focus_routes

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cloud_router, "_load_keys", _REAL_LOAD_KEYS)
    keys = tmp_path / "cloud-keys.env"
    keys.write_text("OPENAI_API_KEY=keepme\n")
    monkeypatch.setattr(cloud_router, "_CLOUD_ENV_FILE", keys)
    assert client.get("/v1/focus/key").json()["configured"] is False
    assert client.post("/v1/focus/key/test").json()["ok"] is False
    bad = client.put("/v1/focus/key", json={"key": "hello"})
    assert bad.status_code == 400
    key = "sk-ant-" + "a" * 40
    saved = client.put("/v1/focus/key", json={"key": f"  {key} "})
    assert saved.json() == {"configured": True, "model": focus_routes.CLOUD_MODEL}
    assert key not in saved.text
    assert keys.read_text() == f"OPENAI_API_KEY=keepme\nANTHROPIC_API_KEY={key}\n"
    assert oct(keys.stat().st_mode & 0o777) == "0o600"

    async def fake_claude(api_key, prompt):
        assert api_key == key
        return "ready"

    monkeypatch.setattr(focus_routes, "_claude", fake_claude)
    assert client.post("/v1/focus/key/test").json()["ok"] is True
    client.put("/v1/focus/key", json={"key": ""})
    assert keys.read_text() == "OPENAI_API_KEY=keepme\n"
    assert client.get("/v1/focus/key").json()["configured"] is False


def test_key_test_reports_a_rejected_key(client, monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")

    from openjarvis.server import cloud_router, focus_routes

    async def rejected(api_key, prompt):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.AuthenticationError(
            "invalid x-api-key",
            response=httpx2.Response(401, request=request),
            body=None,
        )

    monkeypatch.setattr(cloud_router, "_load_keys", lambda: {"ANTHROPIC_API_KEY": "k"})
    monkeypatch.setattr(focus_routes, "_claude", rejected)
    out = client.post("/v1/focus/key/test").json()
    assert out["ok"] is False and "rejected" in out["error"]


def test_calendar_route_and_copying_dated_tasks(client, monkeypatch):
    import time

    from openjarvis.focus import mac_calendar

    created = []
    monkeypatch.setattr(mac_calendar, "available", lambda: True)
    monkeypatch.setattr(
        mac_calendar,
        "read_events",
        lambda: {"events": [{"title": "Standup"}], "source": "database", "note": ""},
    )
    monkeypatch.setattr(mac_calendar, "calendars", lambda: ["Work", "Home"])
    monkeypatch.setattr(mac_calendar, "create_event", lambda *a: created.append(a))
    out = client.get("/v1/focus/calendar").json()
    assert out["events"] == [{"title": "Standup"}] and out["calendars"] == [
        "Work",
        "Home",
    ]
    assert out["write_to"] == ""
    client.post(
        "/v1/focus/task", json={"title": "A", "steps": ["a"], "due": "2026-11-20T15:00"}
    )
    assert created == []
    client.put("/v1/focus/calendar", json={"write_to": "Work"})
    client.post(
        "/v1/focus/task", json={"title": "B", "steps": ["b"], "due": "2026-11-20T15:00"}
    )
    client.post(
        "/v1/focus/task", json={"title": "C", "steps": ["c"], "due": "2026-11-21T23:59"}
    )
    client.post("/v1/focus/task", json={"title": "D", "steps": ["d"]})
    for _ in range(50):
        if len(created) >= 2:
            break
        client.get("/v1/focus/state")
        time.sleep(0.01)
    assert [(c[0], c[1], c[2].isoformat(), c[4]) for c in created] == [
        ("Work", "B", "2026-11-20T15:00:00", False),
        ("Work", "C", "2026-11-21T00:00:00", True),
    ]
    assert client.get("/v1/focus/calendar").json()["write_to"] == "Work"


def test_calendar_route_off_the_mac(client, monkeypatch):
    from openjarvis.focus import mac_calendar

    monkeypatch.setattr(mac_calendar, "available", lambda: False)
    out = client.get("/v1/focus/calendar").json()
    assert out["available"] is False and out["events"] == []


def test_duplicate_calendar_events_are_shown_once(client, monkeypatch):
    from openjarvis.focus import mac_calendar

    events = [
        {
            "title": "Dentist",
            "start": "2026-11-20T15:00:00",
            "all_day": False,
            "calendar": "Gmail",
        },
        {
            "title": "dentist ",
            "start": "2026-11-20T15:00:30",
            "all_day": False,
            "calendar": "Outlook",
        },
        {
            "title": "Dentist",
            "start": "2026-11-21T15:00:00",
            "all_day": False,
            "calendar": "Yahoo",
        },
    ]
    monkeypatch.setattr(mac_calendar, "available", lambda: True)
    monkeypatch.setattr(
        mac_calendar,
        "read_events",
        lambda: {"events": events, "source": "database", "note": ""},
    )
    monkeypatch.setattr(mac_calendar, "calendars", lambda: [])
    out = client.get("/v1/focus/calendar").json()
    assert [(e["calendar"], e["start"][:10]) for e in out["events"]] == [
        ("Gmail", "2026-11-20"),
        ("Yahoo", "2026-11-21"),
    ]


def test_mail_route_and_spoken_summary(client, monkeypatch):
    from openjarvis.focus import mac_mail

    data = {
        "unread": 3,
        "accounts": [{"name": "Gmail", "unread": 3}],
        "latest": [
            {"from": "Ana Ruiz <ana@example.com>", "subject": "Interview", "date": "x"}
        ],
    }
    monkeypatch.setattr(mac_mail, "available", lambda: True)
    monkeypatch.setattr(mac_mail, "read_unread", lambda: data)
    assert client.get("/v1/focus/mail").json()["unread"] == 3
    out = client.post(
        "/v1/focus/command", json={"text": "Morpheus, check my email"}
    ).json()
    assert out["say"] == "You have 3 unread. The newest is from Ana Ruiz: Interview."
    saved = client.app.state.focus_store._path
    assert not saved.exists() or "Interview" not in saved.read_text()


def test_online_model_reads_an_unclear_request(client, monkeypatch):
    from openjarvis.server import cloud_router, focus_routes

    async def fake_claude(api_key, prompt):
        return '{"intent": "task", "text": "call the bank", "when": "friday at 2 pm"}'

    monkeypatch.setattr(cloud_router, "_load_keys", lambda: {"ANTHROPIC_API_KEY": "k"})
    monkeypatch.setattr(focus_routes, "_claude", fake_claude)
    out = client.post(
        "/v1/focus/command", json={"text": "Morpheus, call the bank"}
    ).json()
    assert out["intent"] == "task"
    assert out["say"].startswith("Added Call the bank for Friday")
    task = client.get("/v1/focus/state").json()["tasks"][0]
    assert task["due"].endswith("T14:00")


def test_resume_upload_and_job_search(client, monkeypatch):
    import io
    import zipfile

    from openjarvis.server import cloud_router, focus_routes

    buf = io.BytesIO()
    body = "<w:p><w:t>Process improvement lead &amp; trainer</w:t></w:p>" * 4
    with zipfile.ZipFile(buf, "w") as doc:
        doc.writestr("word/document.xml", f"<w:document>{body}</w:document>")
    replies = iter(
        [
            '["Process Improvement Manager", "Change Manager"]',
        ]
    )

    async def fake_claude(api_key, prompt):
        return next(replies)

    async def fake_search(api_key, prompt):
        assert "Process Improvement Manager" in prompt and "$65,000" in prompt
        return (
            'Here: [{"title": "Process Improvement Manager", "company": "Acme", '
            '"url": "https://example.com/j/1", "salary": "$95k", "description": "x"}, '
            '{"title": "Bad", "url": "javascript:alert(1)"}]'
        )

    monkeypatch.setattr(cloud_router, "_load_keys", lambda: {"ANTHROPIC_API_KEY": "k"})
    monkeypatch.setattr(focus_routes, "_claude", fake_claude)
    monkeypatch.setattr(focus_routes, "_claude_search", fake_search)
    files = {"file": ("cv.docx", buf.getvalue(), "application/octet-stream")}
    up = client.post("/v1/focus/jobs/resume", files=files).json()
    assert up["has_resume"] is True
    assert up["titles"] == ["Process Improvement Manager", "Change Manager"]
    client.put("/v1/focus/jobs/profile", json={"min_salary": 65000})
    assert client.post("/v1/focus/jobs/search").json() == {"added": 1, "found": 2}
    assert client.post("/v1/focus/jobs/search").json()["added"] == 0
    jobs = client.get("/v1/focus/state").json()["jobs"]
    assert [j["company"] for j in jobs] == ["Acme"]
    bad = client.post("/v1/focus/jobs/resume", files={"file": ("cv.pdf", b"x" * 80)})
    assert bad.status_code == 400


def test_business_suggestions_need_a_resume_and_are_saved(client, monkeypatch):
    from openjarvis.server import cloud_router, focus_routes

    async def fake_claude(api_key, prompt):
        assert "candle shop" in prompt and "My CV text" in prompt
        return (
            '[{"name": "Process clinics", "why": "Fits their background.", '
            '"steps": ["Write a one-page offer", "Message five contacts"]}, {"why": "no name"}]'
        )

    monkeypatch.setattr(cloud_router, "_load_keys", lambda: {"ANTHROPIC_API_KEY": "k"})
    monkeypatch.setattr(focus_routes, "_claude", fake_claude)
    assert client.post("/v1/focus/business/suggest").status_code == 409
    client.put("/v1/focus/jobs/profile", json={"resume": "My CV text " * 10})
    client.post("/v1/focus/capture", json={"text": "candle shop", "kind": "idea"})
    out = client.post("/v1/focus/business/suggest").json()
    assert [p["name"] for p in out["plans"]] == ["Process clinics"]
    assert out["plans"][0]["steps"] == [
        "Write a one-page offer",
        "Message five contacts",
    ]
    assert client.get("/v1/focus/business").json() == out
