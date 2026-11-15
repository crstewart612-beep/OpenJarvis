"""Spoken commands for the Focus HUD: wake word, intent and date parsing.

Everything here is rule based so the common commands work with no model at
all. ``interpret`` returns a plain description of what was asked; the
server route carries it out.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

# Speech recognition does not always spell the name the same way.
_WAKE = re.compile(
    r"\b(?:hey|hi|ok|okay|a|eh)?[\s,]*(jarvis|jervis|jarvus|jarvas|jarves|garvis)\b[\s,.!?:]*",
    re.IGNORECASE,
)

_WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]
_MONTHS = {
    name: i + 1
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ]
    )
    for name in names
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_LEAD = r"(?:\b(?:for|on|by|due|this|next|at)\s+)*"
_NUM = r"(\d+(?:\.\d{1,2})?)"


def extract_command(text: str) -> Optional[str]:
    """Text after the wake word; ``""`` for the wake word alone; else None."""
    match = _WAKE.search(text or "")
    if not match:
        return None
    return text[match.end() :].strip()


def normalize(text: str) -> str:
    """Lowercase, tidy a.m./p.m. and drop sentence punctuation."""
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", t)
    t = re.sub(r"(\d),(\d{3})\b", r"\1\2", t)
    t = re.sub(r"[^a-z0-9$.:/' -]+", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    return " ".join(t.split())


def _cut(text: str, match: "re.Match[str]") -> str:
    return (text[: match.start()] + " " + text[match.end() :]).strip()


def _parse_time(text: str) -> Tuple[Optional[Tuple[int, int]], str]:
    m = re.search(_LEAD + r"\b(noon|midnight)\b", text)
    if m:
        return ((12, 0) if m.group(1) == "noon" else (23, 59)), _cut(text, m)
    m = re.search(_LEAD + r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])m\b", text)
    if m:
        hour = int(m.group(1)) % 12 + (12 if m.group(3) == "p" else 0)
        return (hour, int(m.group(2) or 0)), _cut(text, m)
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\b(?!\s*/)", text)
    if m and int(m.group(1)) <= 23:
        hour = int(m.group(1))
        if hour < 8:  # "at 3" in conversation nearly always means 3 PM
            hour += 12
        return (hour, int(m.group(2) or 0)), _cut(text, m)
    return None, text


def _parse_date(text: str, now: datetime) -> Tuple[Optional[datetime], str]:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.search(_LEAD + r"\b(?:the )?day after tomorrow\b", text)
    if m:
        return today + timedelta(days=2), _cut(text, m)
    m = re.search(_LEAD + r"\b(today|tonight|tomorrow)\b", text)
    if m:
        return today + timedelta(days=m.group(1) == "tomorrow"), _cut(text, m)
    m = re.search(_LEAD + r"\bin (\d{1,3}|a|an|one|two|three) (day|week)s?\b", text)
    if m:
        words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}
        n = words.get(m.group(1)) or int(m.group(1))
        return today + timedelta(days=n * (7 if m.group(2) == "week" else 1)), _cut(
            text, m
        )
    m = re.search(_LEAD + r"\b(" + "|".join(_WEEKDAYS) + r")\b", text)
    if m:
        ahead = (_WEEKDAYS.index(m.group(1)) - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead), _cut(text, m)
    m = re.search(
        _LEAD + r"\b(" + _MONTH_RE + r")\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b",
        text,
    ) or re.search(
        _LEAD + r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(" + _MONTH_RE + r")\b",
        text,
    )
    if m:
        a, b = m.group(1), m.group(2)
        month, day = (_MONTHS[a], int(b)) if a in _MONTHS else (_MONTHS[b], int(a))
        return _this_or_next_year(today, month, day), _cut(text, m)
    m = re.search(_LEAD + r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if m.group(3):
            year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
            try:
                return today.replace(year=year, month=month, day=day), _cut(text, m)
            except ValueError:
                return None, text
        return _this_or_next_year(today, month, day), _cut(text, m)
    m = re.search(_LEAD + r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", text)
    if m:
        day = int(m.group(1))
        try:
            when = today.replace(day=day)
        except ValueError:
            return None, text
        if when < today:
            when = (when.replace(day=1) + timedelta(days=32)).replace(day=day)
        return when, _cut(text, m)
    return None, text


def _this_or_next_year(today: datetime, month: int, day: int) -> Optional[datetime]:
    try:
        when = today.replace(month=month, day=day)
    except ValueError:
        return None
    return when if when >= today else when.replace(year=when.year + 1)


def parse_when(text: str, now: datetime) -> Tuple[Optional[datetime], str, bool]:
    """Pull a date and time out of ``text``.

    Returns ``(when, remaining text, has_time)``. A date with no time is due
    at the end of that day; a time with no date means today, or tomorrow if
    that time has already passed.
    """
    tonight = bool(re.search(r"\btonight\b", text))
    clock, rest = _parse_time(text)
    day, rest = _parse_date(rest, now)
    if day is None and clock is None:
        return None, text, False
    if clock is None and tonight:
        clock = (20, 0)
    if day is None:
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        candidate = day.replace(hour=clock[0], minute=clock[1])
        if candidate <= now:
            day += timedelta(days=1)
    if clock is None:
        return day.replace(hour=23, minute=59), rest, False
    return day.replace(hour=clock[0], minute=clock[1]), rest, True


def describe_when(when: datetime, has_time: bool, now: datetime) -> str:
    days = (when.date() - now.date()).days
    if days == 0:
        label = "today"
    elif days == 1:
        label = "tomorrow"
    else:
        label = f"{when.strftime('%A, %B')} {when.day}"
    if not has_time:
        return label
    hour = when.hour % 12 or 12
    minutes = f":{when.minute:02d}" if when.minute else ""
    return f"{label} at {hour}{minutes} {'AM' if when.hour < 12 else 'PM'}"


def _title(text: str) -> str:
    t = re.sub(r"^(?:to|a|an|the|that|my|me)\s+", "", text.strip())
    t = re.sub(r"\s+(?:for|on|by|at|to|in|due)$", "", t).strip(" -:")
    return t[:1].upper() + t[1:]


# Words that stand in for "I have not said what yet".
_VAGUE = {"", "something", "some thing", "a thing", "thing", "an item", "item", "it"}
_VAGUE |= {"this", "that", "stuff", "one", "something else", "a task", "a to do"}
_QUESTION = re.compile(
    r"^(?:what|what's|who|who's|when|where|why|how|is|are|was|do|does|did|can|could|"
    r"will|would|should|tell me|explain|define)\b"
)


def _task(body: str, now: datetime) -> Dict[str, Any]:
    when, rest, has_time = parse_when(body.strip(), now)
    title = _title(rest)
    # Speech recognition sometimes drops words: "add something to do list".
    core = re.sub(
        r"\s*(?:to )?(?:my )?(?:to-?\s?do|todo)?\s*list$", "", normalize(title)
    )
    if core in _VAGUE:
        return {"intent": "ask", "pending": "task"}
    return {
        "intent": "task",
        "title": title,
        "due": when.isoformat(timespec="minutes") if when else None,
        "when": describe_when(when, has_time, now) if when else "",
    }


_LIST = r"(?:my |the |our )?"
_ADD = r"(?:please )?(?:can you |could you )?(?:add|put|save|schedule|set)\s+"


def interpret_pending(
    pending: str, text: str, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Finish a command Jarvis had to ask about ("What should I add?")."""
    now = now or datetime.now()
    t = normalize(text)
    if re.search(r"^(?:never ?mind|nothing|cancel|forget it|no|nope)\b", t):
        return {"intent": "cancel"}
    if pending.startswith("confirm:"):
        if re.search(r"^(?:yes|yeah|yep|sure|please|ok|okay|do it|add it)\b", t):
            return _task(pending[len("confirm:") :], now)
        return {"intent": "cancel"}
    return _task(t, now)


def interpret(text: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Work out what a spoken command is asking for."""
    now = now or datetime.now()
    t = normalize(text)
    if not t:
        return {"intent": "none"}

    if re.search(
        r"^(?:stop listening|go to sleep|that s all|that's all|never ?mind)", t
    ):
        return {"intent": "action", "action": "stop"}
    if re.search(r"\b(?:start|begin|do)\b.*\b(?:sprint|timer|focus session)\b", t):
        return {"intent": "action", "action": "sprint"}
    if re.search(
        r"^(?:step )?done$|^next(?: step)?$|^(?:i )?finished(?: that| it)?$", t
    ):
        return {"intent": "action", "action": "step_done"}
    if re.search(r"\bspanish\b", t) and re.search(
        r"\b(?:start|practice|practise|let's|lets|teach|talk|speak|learn|study|"
        r"review|lesson|continue|resume)\b",
        t,
    ):
        return {"intent": "action", "action": "spanish"}

    m = re.search(
        r"(?:add|record|put|log)\s+\$?" + _NUM + r"\s*(?:dollars|bucks)?\s+"
        r"(?:to|toward|towards|on|in)\s+"
        + _LIST
        + r"(?:car goal|goal|car|savings)\b(.*)",
        t,
    )
    if m:
        return {
            "intent": "goal",
            "amount": float(m.group(1)),
            "note": _title(m.group(2)),
        }
    m = re.search(
        r"\bi (made|won|earned|lost|am down|was down)\s+\$?" + _NUM + r"\b(.*)", t
    )
    if m:
        sign = -1 if m.group(1) in ("lost", "am down", "was down") else 1
        note = re.sub(r"^\s*(?:dollars|bucks)\b", "", m.group(3))
        return {
            "intent": "goal",
            "amount": sign * float(m.group(2)),
            "note": _title(note),
        }

    if re.search(r"\b(?:e-?mails?|inbox)\b", t):
        return {"intent": "email"}

    m = re.search(
        "^" + _ADD + r"(.+?)\s+(?:to|in|on)\s+" + _LIST + r"ideas?(?: list| library)?$",
        t,
    ) or re.search(r"^(?:i have an? |new )?idea(?: is| for)?[: ]+(.+)$", t)
    if m:
        return {"intent": "capture", "kind": "idea", "text": _title(m.group(1))}
    m = re.search(
        "^"
        + _ADD
        + r"(.+?)\s+(?:to|in|on)\s+"
        + _LIST
        + r"(?:brain ?dump|notes?|quick capture|capture)$",
        t,
    ) or re.search(
        r"^(?:brain ?dump|note|remember|capture|jot down|write down)"
        r"(?: that)?[: ]+(.+)$",
        t,
    )
    if m:
        return {"intent": "capture", "kind": "note", "text": _title(m.group(1))}

    place = (
        r"\s+(?:to|on|in)\s+"
        + _LIST
        + r"(?:calendar|to-?\s?do(?:s| list)?|task list|tasks?|list|schedule)\b"
    )
    m = re.search("^" + _ADD + r"(.+?)" + place + r"(.*)$", t)
    if m:
        return _task(f"{m.group(1)} {m.group(2)}", now)
    m = re.search(
        r"^(?:remind me|i need|i have|i've got|i got|i must|i want|i should|"
        r"don't forget|do not forget)\s+(?:to |about )?(.+)$",
        t,
    ) or re.search(
        "^" + _ADD + r"(?:a |an |my |the )?(?:new )?"
        r"(?:(?:to-?\s?do|task|reminder|event)\s+)?(?:to |for |about |called )?(.+)$",
        t,
    )
    if m:
        return _task(m.group(1), now)
    if _QUESTION.search(t):
        return {"intent": "question", "text": text.strip()}
    # A plain statement such as "I'm picking up laundry tomorrow". If it
    # carries a date it is treated as something to do; otherwise ask first.
    statement = re.sub(
        r"^(?:i'm|i am|i'll|i will|we're|we are|we will|i'm going to|"
        r"i am going to|going to|gonna)\s+",
        "",
        t,
    )
    when, _rest, _timed = parse_when(statement, now)
    if when is not None:
        return _task(statement, now)
    return {"intent": "confirm", "title": _title(statement)}
