"""Beginner Mexican Spanish with rolling, mastery-based progression.

A unit does not unlock until every item in the unit before it is mastered.
An item is mastered after three correct answers in a row; a miss sends it
back to the start. Only a few items are in play at once, so new material
arrives as older material is learned.
"""

from __future__ import annotations

import difflib
import random
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

MASTERED = 3
IN_PLAY = 4
DAILY_GOAL_MINUTES = 60
_MAX_GAP_SECONDS = 90

# (unit id, title, [(spanish, english), ...])
_UNITS: List[Tuple[str, str, List[Tuple[str, str]]]] = [
    (
        "greetings",
        "Greetings",
        [
            ("hola", "hello"),
            ("buenos días", "good morning"),
            ("buenas tardes", "good afternoon"),
            ("buenas noches", "good night"),
            ("¿cómo estás?", "how are you?"),
            ("bien, gracias", "fine, thank you"),
            ("por favor", "please"),
            ("gracias", "thank you"),
            ("de nada", "you're welcome"),
            ("adiós", "goodbye"),
            ("hasta luego", "see you later"),
            ("mucho gusto", "nice to meet you"),
        ],
    ),
    (
        "numbers-1",
        "Numbers 1 to 10",
        [
            ("uno", "one"),
            ("dos", "two"),
            ("tres", "three"),
            ("cuatro", "four"),
            ("cinco", "five"),
            ("seis", "six"),
            ("siete", "seven"),
            ("ocho", "eight"),
            ("nueve", "nine"),
            ("diez", "ten"),
        ],
    ),
    (
        "numbers-2",
        "Numbers 0 and 11 to 20",
        [
            ("cero", "zero"),
            ("once", "eleven"),
            ("doce", "twelve"),
            ("trece", "thirteen"),
            ("catorce", "fourteen"),
            ("quince", "fifteen"),
            ("dieciséis", "sixteen"),
            ("diecisiete", "seventeen"),
            ("dieciocho", "eighteen"),
            ("diecinueve", "nineteen"),
            ("veinte", "twenty"),
        ],
    ),
    (
        "about-you",
        "About you",
        [
            ("me llamo Ana", "my name is Ana"),
            ("¿cómo te llamas?", "what is your name?"),
            ("soy de Estados Unidos", "I am from the United States"),
            ("¿de dónde eres?", "where are you from?"),
            ("vivo en México", "I live in Mexico"),
            ("hablo un poco de español", "I speak a little Spanish"),
            ("no entiendo", "I don't understand"),
            ("¿puede repetir?", "can you repeat that?"),
            ("más despacio, por favor", "slower, please"),
            ("¿habla inglés?", "do you speak English?"),
        ],
    ),
    (
        "essentials",
        "Essential words",
        [
            ("sí", "yes"),
            ("no", "no"),
            ("¿qué?", "what?"),
            ("¿dónde?", "where?"),
            ("¿cuándo?", "when?"),
            ("¿cuánto?", "how much?"),
            ("¿por qué?", "why?"),
            ("aquí", "here"),
            ("allá", "over there"),
            ("hoy", "today"),
            ("mañana", "tomorrow"),
            ("ahora", "now"),
        ],
    ),
    (
        "shopping",
        "Shopping",
        [
            ("¿cuánto cuesta?", "how much does it cost?"),
            ("quiero esto", "I want this"),
            ("es muy caro", "it is very expensive"),
            ("¿acepta tarjeta?", "do you take cards?"),
            ("en efectivo", "in cash"),
            ("la cuenta, por favor", "the check, please"),
            ("el cambio", "the change"),
            ("una bolsa", "a bag"),
            ("abierto", "open"),
            ("cerrado", "closed"),
        ],
    ),
    (
        "food",
        "Food and ordering",
        [
            ("agua", "water"),
            ("café", "coffee"),
            ("pan", "bread"),
            ("pollo", "chicken"),
            ("carne", "meat"),
            ("sin picante", "not spicy"),
            ("para llevar", "to go"),
            ("el desayuno", "breakfast"),
            ("la comida", "lunch"),
            ("la cena", "dinner"),
            ("buen provecho", "enjoy your meal"),
        ],
    ),
    (
        "getting-around",
        "Getting around",
        [
            ("¿dónde está el baño?", "where is the bathroom?"),
            ("a la derecha", "to the right"),
            ("a la izquierda", "to the left"),
            ("derecho", "straight ahead"),
            ("cerca", "near"),
            ("lejos", "far"),
            ("la calle", "the street"),
            ("la farmacia", "the pharmacy"),
            ("el banco", "the bank"),
            ("necesito ayuda", "I need help"),
        ],
    ),
    (
        "days",
        "Days and time",
        [
            ("lunes", "Monday"),
            ("martes", "Tuesday"),
            ("miércoles", "Wednesday"),
            ("jueves", "Thursday"),
            ("viernes", "Friday"),
            ("sábado", "Saturday"),
            ("domingo", "Sunday"),
            ("la semana", "the week"),
            ("¿qué hora es?", "what time is it?"),
        ],
    ),
]


def _item_id(unit_id: str, index: int) -> str:
    return f"{unit_id}:{index}"


UNITS: List[Dict[str, Any]] = [
    {
        "id": uid,
        "title": title,
        "items": [
            {"id": _item_id(uid, i), "es": es, "en": en}
            for i, (es, en) in enumerate(pairs)
        ],
    }
    for uid, title, pairs in _UNITS
]
_ITEMS: Dict[str, Dict[str, Any]] = {
    item["id"]: {**item, "unit": unit["id"]} for unit in UNITS for item in unit["items"]
}


def normalize(text: str) -> str:
    """Lowercase and drop accents and punctuation so typing is forgiving."""
    stripped = unicodedata.normalize("NFD", text or "")
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    stripped = re.sub(r"[^a-z0-9 ]+", " ", stripped.lower())
    return " ".join(stripped.split())


def new_state() -> Dict[str, Any]:
    return {"items": {}, "days": {}, "last_at": None, "last_item": None}


def _rec(state: Dict[str, Any], item_id: str) -> Dict[str, Any]:
    return state["items"].setdefault(item_id, {"streak": 0, "seen": False})


def _mastered(state: Dict[str, Any], item_id: str) -> bool:
    return state["items"].get(item_id, {}).get("streak", 0) >= MASTERED


def current_unit(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The first unit that still has an item to master."""
    for unit in UNITS:
        if not all(_mastered(state, i["id"]) for i in unit["items"]):
            return unit
    return None


def _choices(item: Dict[str, Any], streak: int) -> List[str]:
    unit = next(u for u in UNITS if u["id"] == item["unit"])
    others = [i["en"] for i in unit["items"] if i["id"] != item["id"]]
    rng = random.Random(f"{item['id']}:{streak}")
    picks = rng.sample(others, 3) + [item["en"]]
    rng.shuffle(picks)
    return picks


def next_card(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Next thing to show, or ``None`` when every unit is mastered."""
    unit = current_unit(state)
    if unit is None:
        return None
    open_items = [i for i in unit["items"] if not _mastered(state, i["id"])]
    in_play = [i for i in open_items if state["items"].get(i["id"], {}).get("seen")]
    unseen = [i for i in open_items if i not in in_play]
    if unseen and len(in_play) < IN_PLAY:
        item = unseen[0]
    else:
        pool = [i for i in in_play if i["id"] != state.get("last_item")] or in_play
        item = min(pool, key=lambda i: state["items"][i["id"]]["streak"])
    rec = state["items"].get(item["id"], {"streak": 0, "seen": False})
    full = _ITEMS[item["id"]]
    card: Dict[str, Any] = {
        "item_id": item["id"],
        "unit": unit["title"],
        "es": item["es"],
        "en": item["en"],
    }
    if not rec["seen"]:
        card["kind"] = "intro"
    elif rec["streak"] < MASTERED - 1:
        card["kind"] = "choose"
        card["choices"] = _choices(full, rec["streak"])
    else:
        card["kind"] = "type"
    return card


def _add_time(state: Dict[str, Any], now: datetime) -> None:
    last = state.get("last_at")
    if last:
        gap = (now - datetime.fromisoformat(last)).total_seconds()
        if 0 < gap:
            key = now.date().isoformat()
            state["days"][key] = state["days"].get(key, 0) + min(gap, _MAX_GAP_SECONDS)
    state["last_at"] = now.isoformat(timespec="seconds")


def answer(
    state: Dict[str, Any],
    item_id: str,
    action: str,
    text: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Apply one response. ``action``: seen, answer, spoken, skip, skip_unit."""
    if item_id not in _ITEMS:
        raise ValueError(f"Unknown item: {item_id}")
    item = _ITEMS[item_id]
    rec = _rec(state, item_id)
    _add_time(state, now or datetime.now())
    state["last_item"] = item_id
    result: Dict[str, Any] = {"correct": None, "es": item["es"], "en": item["en"]}
    if action == "seen":
        rec["seen"] = True
    elif action == "spoken":
        rec["seen"] = True
        rec["streak"] = min(MASTERED, rec["streak"] + 1)
        result["correct"] = True
    elif action == "skip":
        rec.update(seen=True, streak=MASTERED)
    elif action == "skip_unit":
        unit = next(u for u in UNITS if u["id"] == item["unit"])
        for other in unit["items"]:
            _rec(state, other["id"]).update(seen=True, streak=MASTERED)
    elif action == "answer":
        given = normalize(text)
        typing = rec["streak"] >= MASTERED - 1
        expected = normalize(item["es"] if typing else item["en"])
        correct = bool(given) and given == expected
        rec["seen"] = True
        rec["streak"] = rec["streak"] + 1 if correct else 0
        result["correct"] = correct
    else:
        raise ValueError(f"Unknown action: {action}")
    return result


def heard_matches(item_id: str, heard: str) -> bool:
    """True when the recognised speech contains the item's Spanish."""
    expected = normalize(_ITEMS[item_id]["es"])
    said = normalize(heard)
    return bool(said) and f" {expected} " in f" {said} "


_VOICE_COMMANDS = {
    "stop": ("stop", "that's enough", "thats enough", "i'm done", "im done", "exit")
    + ("quit", "para", "alto", "basta", "end the lesson", "finish"),
    "skip": ("skip", "i know this", "i know that", "i know it", "next", "saltar"),
    "repeat": ("repeat", "again", "say it again", "one more time", "otra vez")
    + ("repite", "repeat that", "say that again"),
}


def spoken_command(heard: str) -> Optional[str]:
    """A lesson command said aloud instead of an answer, if any."""
    said = normalize(heard.replace("'", ""))
    for name, phrases in _VOICE_COMMANDS.items():
        for phrase in phrases:
            key = normalize(phrase.replace("'", ""))
            if said == key or said.startswith(key + " "):
                return name
    return None


def close_enough(expected: str, heard: str, threshold: float = 0.8) -> bool:
    """Forgiving match for speech: contained, or nearly the same letters."""
    want = normalize(expected.replace("'", ""))
    said = normalize(heard.replace("'", ""))
    if not want or not said:
        return False
    if f" {want} " in f" {said} ":
        return True
    return difflib.SequenceMatcher(None, want, said).ratio() >= threshold


def check_voice(item_id: str, kind: str, heard: str) -> bool:
    """Was the spoken answer right? ``choose`` cards are answered in English."""
    item = _ITEMS[item_id]
    return close_enough(item["en"] if kind == "choose" else item["es"], heard)


def known_phrases(state: Dict[str, Any], limit: int = 60) -> List[str]:
    """Spanish the learner has met so far, for keeping conversation simple."""
    seen = [
        _ITEMS[i]["es"]
        for i, rec in state.get("items", {}).items()
        if rec.get("seen") and i in _ITEMS
    ]
    return seen[-limit:]


def touch(state: Dict[str, Any], now: Optional[datetime] = None) -> None:
    """Count practice time for activity that is not a card answer."""
    _add_time(state, now or datetime.now())


def progress(state: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    unit = current_unit(state)
    days = state.get("days", {})

    def practised(d: date) -> bool:
        return days.get(d.isoformat(), 0) >= 60

    streak = 0
    day = today if practised(today) else today - timedelta(days=1)
    while practised(day):
        streak += 1
        day -= timedelta(days=1)
    out: Dict[str, Any] = {
        "words_mastered": sum(1 for i in _ITEMS if _mastered(state, i)),
        "words_total": len(_ITEMS),
        "minutes_today": int(days.get(today.isoformat(), 0) // 60),
        "goal_minutes": DAILY_GOAL_MINUTES,
        "day_streak": streak,
        "unit": unit["title"] if unit else "All units complete",
        "unit_number": UNITS.index(unit) + 1 if unit else len(UNITS),
        "unit_count": len(UNITS),
    }
    if unit:
        out["unit_mastered"] = sum(
            1 for i in unit["items"] if _mastered(state, i["id"])
        )
        out["unit_total"] = len(unit["items"])
    return out
