from datetime import datetime

import pytest

from openjarvis.focus.commands import (
    extract_command,
    interpret,
    interpret_pending,
    parse_when,
)

NOW = datetime(2026, 11, 15, 10, 0)  # a Sunday


@pytest.mark.parametrize(
    "heard,expected",
    [
        ("Hey Jarvis, add milk to my list.", "add milk to my list."),
        ("hey jervis start a sprint", "start a sprint"),
        ("Jarvis.", ""),
        ("Hey Morpheus, add milk to my list.", "add milk to my list."),
        ("hey morfius start a sprint", "start a sprint"),
        ("OK, so, hey Jarvis: remind me to call mom", "remind me to call mom"),
        ("I was talking about the weather", None),
    ],
)
def test_extract_command(heard, expected):
    assert extract_command(heard) == expected


@pytest.mark.parametrize(
    "text,iso,has_time",
    [
        ("tomorrow at 3 PM", "2026-11-16T15:00", True),
        ("on friday", "2026-11-20T23:59", False),
        ("for November 20th at 9:30 a.m.", "2026-11-20T09:30", True),
        ("january 5", "2027-01-05T23:59", False),
        ("at 3", "2026-11-15T15:00", True),
        ("at 9 am", "2026-11-16T09:00", True),
        ("in 3 days", "2026-11-18T23:59", False),
        ("12/1 at noon", "2026-12-01T12:00", True),
        ("the 20th", "2026-11-20T23:59", False),
        ("tonight", "2026-11-15T20:00", True),
        ("sunday", "2026-11-22T23:59", False),
    ],
)
def test_parse_when(text, iso, has_time):
    from openjarvis.focus.commands import normalize

    when, _rest, timed = parse_when(normalize(text), NOW)
    assert when.isoformat(timespec="minutes") == iso
    assert timed is has_time


def test_calendar_command_with_date_and_time():
    out = interpret("Add dentist appointment to my calendar for Friday at 3 PM.", NOW)
    assert out == {
        "intent": "task",
        "title": "Dentist appointment",
        "due": "2026-11-20T15:00",
        "when": "Friday, November 20 at 3 PM",
    }


def test_task_variants():
    assert interpret("remind me to call mom tomorrow", NOW)["title"] == "Call mom"
    assert interpret("remind me to call mom tomorrow", NOW)["when"] == "tomorrow"
    out = interpret("add pay rent on December 1st to my to-do list", NOW)
    assert (out["title"], out["due"]) == ("Pay rent", "2026-12-01T23:59")
    out = interpret("put buy groceries on my list", NOW)
    assert (out["title"], out["due"]) == ("Buy groceries", None)
    assert interpret("I need to renew my passport", NOW)["title"] == "Renew my passport"


def test_capture_and_idea():
    out = interpret("Add find hair care products to my brain dump", NOW)
    assert out == {
        "intent": "capture",
        "kind": "note",
        "text": "Find hair care products",
    }
    out = interpret("add a weekend candle pop-up to my idea list", NOW)
    assert out == {"intent": "capture", "kind": "idea", "text": "Weekend candle pop-up"}
    assert interpret("note that the plumber comes Tuesday", NOW)["kind"] == "note"
    assert interpret("I have an idea: sell meal prep templates", NOW)["kind"] == "idea"


def test_goal_amounts():
    assert interpret("add $45.50 to my car goal", NOW)["amount"] == 45.5
    out = interpret("I made 60 dollars on MES", NOW)
    assert (out["intent"], out["amount"], out["note"]) == ("goal", 60.0, "On mes")
    assert interpret("I lost $20", NOW)["amount"] == -20.0
    assert interpret("record 1,200 toward my goal", NOW)["amount"] == 1200.0


def test_actions_email_and_unknown():
    assert interpret("start a sprint", NOW) == {"intent": "action", "action": "sprint"}
    assert interpret("Done.", NOW) == {"intent": "action", "action": "step_done"}
    assert interpret("let's practice Spanish", NOW)["action"] == "spanish"
    assert interpret("stop listening", NOW)["action"] == "stop"
    assert interpret("group my emails", NOW) == {"intent": "email"}
    assert interpret("what is twelve times eight", NOW)["intent"] == "question"
    assert interpret("", NOW) == {"intent": "none"}


@pytest.mark.parametrize(
    "said",
    [
        "I'm picking up laundry tomorrow",
        "add my picking up laundry tomorrow",
        "pick up laundry tomorrow",
        "I need to pick up my laundry tomorrow",
        "add picking up my laundry tomorrow to my to-do list",
    ],
)
def test_natural_phrasing_becomes_a_dated_task(said):
    out = interpret(said, NOW)
    assert out["intent"] == "task"
    assert "laundry" in out["title"].lower()
    assert out["due"] == "2026-11-16T23:59"


@pytest.mark.parametrize(
    "said",
    ["add something to my to do list", "Add something to do list.", "add a thing"],
)
def test_vague_request_asks_what_to_add(said):
    assert interpret(said, NOW) == {"intent": "ask", "pending": "task"}


def test_follow_up_answers():
    out = interpret_pending("task", "pick up laundry tomorrow", NOW)
    assert (out["title"], out["when"]) == ("Pick up laundry", "tomorrow")
    assert interpret_pending("task", "never mind", NOW) == {"intent": "cancel"}
    assert interpret("the sky looks nice", NOW)["intent"] == "confirm"
    yes = interpret_pending("confirm:call the bank", "yes please", NOW)
    assert yes["title"] == "Call the bank"
    assert interpret_pending("confirm:call the bank", "uh", NOW)["intent"] == "cancel"


def test_spanish_requests():
    for said in ("teach me Spanish", "I want to learn Spanish", "continue my Spanish"):
        assert interpret(said, NOW) == {"intent": "action", "action": "spanish"}
