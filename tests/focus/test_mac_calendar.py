import json
from datetime import datetime

import pytest

from openjarvis.focus import mac_calendar


def test_create_event_passes_values_as_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mac_calendar, "_osascript", lambda *a, **k: calls.append(a) or "ok"
    )
    mac_calendar.create_event(
        'Work"; evil()', "Dentist", datetime(2026, 11, 20, 15, 0), 45
    )
    script, calendar, title, start, minutes, all_day = calls[0]
    assert "evil" not in script
    assert (calendar, title) == ('Work"; evil()', "Dentist")
    assert (start, minutes, all_day) == ("2026-11-20T15:00:00", "45", "0")


def test_read_events_falls_back_to_the_calendar_app(monkeypatch):
    def no_database(days):
        raise mac_calendar.CalendarError("not readable")

    events = [
        {
            "title": "B",
            "start": "2026-11-20T15:00:00.000Z",
            "all_day": False,
            "calendar": "Work",
        },
        {
            "title": "A",
            "start": "2026-11-16T09:00:00.000Z",
            "all_day": False,
            "calendar": "Home",
        },
    ]
    monkeypatch.setattr(mac_calendar, "_from_database", no_database)
    monkeypatch.setattr(mac_calendar, "_osascript", lambda *a, **k: json.dumps(events))
    out = mac_calendar.read_events(now=datetime(2026, 11, 15, 8, 0))
    assert [e["title"] for e in out["events"]] == ["A", "B"]
    assert out["source"] == "calendar-app" and "Repeating" in out["note"]


def test_read_events_prefers_the_database(monkeypatch):
    rows = [
        {
            "title": "Standup",
            "start": "2026-11-16T09:00:00",
            "all_day": False,
            "calendar": "Work",
        }
    ]
    monkeypatch.setattr(mac_calendar, "_from_database", lambda days: rows)
    out = mac_calendar.read_events()
    assert out["source"] == "database" and out["note"] == ""


def test_permission_error_is_explained(monkeypatch):
    class Done:
        returncode = 1
        stdout = ""
        stderr = (
            "execution error: Not authorized to send Apple events to Calendar. (-1743)"
        )

    monkeypatch.setattr(mac_calendar.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(mac_calendar.CalendarError, match="Automation"):
        mac_calendar.calendars()
