from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.focus.store import FocusStore  # noqa: E402
from openjarvis.server.focus_routes import focus_router, parse_steps  # noqa: E402


class _Engine:
    def generate(self, messages, *, model, **kwargs):
        return {"content": 'Sure:\n["Open mail", "Attach the PDF", "Press send"]'}


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(focus_router)
    app.state.focus_store = FocusStore(tmp_path / "focus.json")
    app.state.engine = _Engine()
    app.state.model = "test"
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
