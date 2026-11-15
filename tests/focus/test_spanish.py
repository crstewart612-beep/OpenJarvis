from datetime import date, datetime, timedelta

from openjarvis.focus import spanish


def _drive(state, item_id, n=3):
    spanish.answer(state, item_id, "seen")
    for _ in range(n):
        item = spanish._ITEMS[item_id]
        typing = state["items"][item_id]["streak"] >= spanish.MASTERED - 1
        spanish.answer(state, item_id, "answer", item["es"] if typing else item["en"])


def test_normalize_is_forgiving():
    assert spanish.normalize("¿Cómo estás?") == "como estas"
    assert spanish.normalize("  Buenos   DÍAS ") == "buenos dias"


def test_starts_with_intro_then_choose_then_type():
    state = spanish.new_state()
    card = spanish.next_card(state)
    assert card["kind"] == "intro" and card["es"] == "hola"
    spanish.answer(state, card["item_id"], "seen")
    state["last_item"] = None
    state["items"]["greetings:0"]["seen"] = True
    # Force this item to be the only one in play to follow its ladder.
    for i in range(1, 12):
        state["items"][f"greetings:{i}"] = {"streak": spanish.MASTERED, "seen": True}
    card = spanish.next_card(state)
    assert card["kind"] == "choose" and "hello" in card["choices"]
    assert len(set(card["choices"])) == 4
    spanish.answer(state, "greetings:0", "answer", "hello")
    spanish.answer(state, "greetings:0", "answer", "Hello")
    assert spanish.next_card(state)["kind"] == "type"
    assert spanish.answer(state, "greetings:0", "answer", "HOLA")["correct"] is True
    assert spanish.current_unit(state)["id"] == "numbers-1"


def test_miss_resets_and_unit_does_not_advance():
    state = spanish.new_state()
    spanish.answer(state, "greetings:0", "seen")
    spanish.answer(state, "greetings:0", "answer", "hello")
    assert state["items"]["greetings:0"]["streak"] == 1
    out = spanish.answer(state, "greetings:0", "answer", "goodbye")
    assert out["correct"] is False and out["es"] == "hola"
    assert state["items"]["greetings:0"]["streak"] == 0
    assert spanish.current_unit(state)["id"] == "greetings"


def test_only_a_few_items_in_play():
    state = spanish.new_state()
    seen = set()
    for _ in range(spanish.IN_PLAY):
        card = spanish.next_card(state)
        assert card["kind"] == "intro"
        seen.add(card["item_id"])
        spanish.answer(state, card["item_id"], "seen")
    assert len(seen) == spanish.IN_PLAY
    assert spanish.next_card(state)["kind"] == "choose"


def test_skip_and_skip_unit():
    state = spanish.new_state()
    spanish.answer(state, "greetings:0", "skip")
    assert state["items"]["greetings:0"]["streak"] == spanish.MASTERED
    spanish.answer(state, "greetings:1", "skip_unit")
    assert spanish.current_unit(state)["id"] == "numbers-1"


def test_finishing_everything_returns_no_card():
    state = spanish.new_state()
    for unit in spanish.UNITS:
        spanish.answer(state, unit["items"][0]["id"], "skip_unit")
    assert spanish.next_card(state) is None
    assert spanish.progress(state)["words_mastered"] == len(spanish._ITEMS)


def test_time_and_streak():
    state = spanish.new_state()
    t0 = datetime(2026, 11, 14, 10, 0, 0)
    for i in range(5):
        spanish.answer(state, "greetings:0", "seen", now=t0 + timedelta(seconds=30 * i))
    # A long pause counts for at most 90 seconds.
    spanish.answer(state, "greetings:0", "seen", now=t0 + timedelta(hours=1))
    prog = spanish.progress(state, today=date(2026, 11, 14))
    assert prog["minutes_today"] == 3
    assert prog["day_streak"] == 1
    assert spanish.progress(state, today=date(2026, 11, 15))["day_streak"] == 1
    assert spanish.progress(state, today=date(2026, 11, 16))["day_streak"] == 0


def test_every_unit_has_enough_items_for_choices():
    for unit in spanish.UNITS:
        assert len(unit["items"]) >= 4
        assert len({i["en"] for i in unit["items"]}) == len(unit["items"])


def test_heard_matches_ignores_accents_and_extra_words():
    assert spanish.heard_matches("greetings:1", "Buenos dias.")
    assert spanish.heard_matches("greetings:0", "eh, hola!")
    assert not spanish.heard_matches("greetings:0", "ola")
    assert not spanish.heard_matches("greetings:0", "")


def test_spoken_counts_as_correct_and_known_phrases():
    state = spanish.new_state()
    out = spanish.answer(state, "greetings:0", "spoken")
    assert out["correct"] is True
    assert state["items"]["greetings:0"] == {"streak": 1, "seen": True}
    for _ in range(5):
        spanish.answer(state, "greetings:0", "spoken")
    assert state["items"]["greetings:0"]["streak"] == spanish.MASTERED
    assert spanish.known_phrases(state) == ["hola"]
