"""Read and add events through the Mac's own Calendar app.

Accounts added to macOS (Google, Outlook, iCloud) all show up in Calendar,
so talking to Calendar reaches every one of them with no cloud credentials.
Reading prefers the Calendar database, which knows about repeating events
but needs Full Disk Access; without it, Calendar is asked directly, which
works after a one-time permission prompt but misses repeats.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_LIST_CALENDARS = """
function run() {
  const cal = Application('Calendar');
  return JSON.stringify(cal.calendars().filter(c => c.writable()).map(c => c.name()));
}
"""

_READ_EVENTS = """
function run(argv) {
  const cal = Application('Calendar');
  const start = new Date(argv[0]);
  const end = new Date(argv[1]);
  const out = [];
  cal.calendars().forEach(c => {
    const found = c.events.whose({_and: [
      {startDate: {_greaterThan: start}}, {startDate: {_lessThan: end}},
    ]})();
    found.forEach(e => out.push({
      title: e.summary(), start: e.startDate().toISOString(),
      all_day: e.alldayEvent(), calendar: c.name(),
    }));
  });
  return JSON.stringify(out);
}
"""

_CREATE_EVENT = """
function run(argv) {
  const cal = Application('Calendar');
  const target = cal.calendars.byName(argv[0]);
  const start = new Date(argv[2]);
  const end = new Date(start.getTime() + Number(argv[3]) * 60000);
  const event = cal.Event({
    summary: argv[1], startDate: start, endDate: end, alldayEvent: argv[4] === '1',
  });
  target.events.push(event);
  return 'ok';
}
"""


class CalendarError(RuntimeError):
    """Calendar could not be reached; the message is safe to show."""


def available() -> bool:
    return sys.platform == "darwin"


def _osascript(script: str, *args: str, timeout: float = 30.0) -> str:
    """Run a JavaScript-for-Automation script; arguments are passed, not spliced."""
    try:
        done = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CalendarError("The Calendar app did not answer.") from exc
    if done.returncode != 0:
        detail = (done.stderr or "").strip()
        if "-1743" in detail or "not allowed" in detail.lower():
            raise CalendarError(
                "macOS has not allowed access to Calendar yet. Open System Settings, "
                "Privacy & Security, Automation, and switch Calendar on for Terminal."
            )
        logger.warning("osascript failed: %s", detail[:300])
        raise CalendarError("The Calendar app returned an error.")
    return done.stdout.strip()


def calendars() -> List[str]:
    """Names of calendars new events can be added to."""
    return list(json.loads(_osascript(_LIST_CALENDARS) or "[]"))


def _from_database(days: int) -> List[Dict[str, Any]]:
    from openjarvis.connectors.apple_calendar import (
        AppleCalendarConnector,
        _open_db,
    )

    connector = AppleCalendarConnector(days_ahead=days, days_behind=0)
    conn = _open_db(connector._db_path)
    if conn is None:
        raise CalendarError("The Calendar database is not readable.")
    try:
        conn.execute("SELECT 1 FROM CalendarItem LIMIT 1")
    except Exception as exc:
        raise CalendarError("The Calendar database is not readable.") from exc
    finally:
        conn.close()
    return [
        {
            "title": doc.metadata.get("summary") or doc.title or "(no title)",
            "start": doc.metadata["start"],
            "all_day": bool(doc.metadata.get("all_day")),
            "calendar": doc.metadata.get("calendar") or "",
        }
        for doc in connector.sync()
    ]


def read_events(days: int = 7, now: datetime | None = None) -> Dict[str, Any]:
    """Upcoming events, soonest first, and where they were read from."""
    now = now or datetime.now()
    note = ""
    try:
        events, source = _from_database(days), "database"
    except Exception:
        start = now - timedelta(minutes=1)
        end = now + timedelta(days=days)
        raw = _osascript(
            _READ_EVENTS, start.isoformat(), end.isoformat(), timeout=180.0
        )
        events, source = list(json.loads(raw or "[]")), "calendar-app"
        note = (
            "Repeating events may be missing. Giving Terminal Full Disk Access "
            "shows them all."
        )
    events.sort(key=lambda e: _sort_key(e["start"]))
    return {"events": events[:40], "source": source, "note": note}


def _sort_key(iso: str) -> float:
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return when.timestamp() if when.tzinfo else when.astimezone().timestamp()


def create_event(
    calendar: str, title: str, start: datetime, minutes: int = 30, all_day: bool = False
) -> None:
    """Add one event to the named calendar."""
    _osascript(
        _CREATE_EVENT,
        calendar,
        title,
        start.strftime("%Y-%m-%dT%H:%M:%S"),
        str(minutes),
        "1" if all_day else "0",
    )
