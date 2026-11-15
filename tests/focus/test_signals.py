import pytest

from openjarvis.focus.signals import (
    CONTRACTS,
    evaluate,
    max_stop_points,
    normalize_symbol,
    plan_trade,
    position_size,
)


def test_point_values():
    assert CONTRACTS["MES"].point_value == 5
    assert CONTRACTS["MNQ"].point_value == 2
    assert CONTRACTS["MYM"].point_value == 0.5
    assert CONTRACTS["M2K"].point_value == 5


def test_five_dollar_risk_stop_widths():
    assert max_stop_points("MES", 5) == 1.0
    assert max_stop_points("MNQ", 5) == 2.5
    assert max_stop_points("MYM", 5) == 10
    assert max_stop_points("M2K", 5) == 1.0


def test_position_size_never_exceeds_risk():
    assert position_size("MYM", 5, 10) == 1
    assert position_size("MYM", 5, 11) == 0
    assert position_size("MES", 50, 4) == 2
    assert position_size("MES", 5, 0) == 0


def test_unknown_contract():
    with pytest.raises(ValueError):
        position_size("NES", 5, 1)


def _bars(closes, spread=1.0):
    return [{"high": c + spread, "low": c - spread, "close": c} for c in closes]


def test_wait_without_history():
    assert evaluate("MES", _bars([1, 2, 3]), 5).action == "wait"


def test_fresh_cross_up_is_long_and_sized():
    closes = [100.0] * 30 + [99.0] * 5 + [110.0]
    sig = evaluate("MYM", _bars(closes, spread=1.0), 50)
    assert sig.action == "long"
    assert sig.contracts >= 1
    assert sig.risk_dollars <= 50


def test_signal_that_does_not_fit_risk_is_wait():
    closes = [100.0] * 30 + [99.0] * 5 + [110.0]
    sig = evaluate("MES", _bars(closes, spread=1.0), 5)
    assert sig.action == "wait"
    assert sig.contracts == 0
    assert "limit" in sig.reason


def test_normalize_symbol():
    assert normalize_symbol("MES1!") == "MES"
    assert normalize_symbol("CME_MINI:MNQZ2026") == "MNQ"
    with pytest.raises(ValueError):
        normalize_symbol("NES")


def test_plan_trade_long_within_risk():
    plan = plan_trade("MES1!", "long", 5000.0, 2.0, 20)
    assert plan["action"] == "long"
    assert plan["contracts"] == 1
    assert plan["stop_points"] == 3.0
    assert plan["stop"] == 4997.0
    assert plan["target"] == 5006.0
    assert plan["trail_at"] == 5003.0
    assert plan["risk_dollars"] == 15.0


def test_plan_trade_short_mirrors_levels():
    plan = plan_trade("MYM", "short", 40000, 10, 20)
    assert plan["contracts"] == 2
    assert plan["stop"] == 40015
    assert plan["target"] == 39970
    assert plan["risk_dollars"] == 15.0


def test_plan_trade_waits_when_stop_exceeds_risk():
    plan = plan_trade("MES", "long", 5000.0, 4.0, 20)
    assert plan["action"] == "wait"
    assert plan["contracts"] == 0
    assert "Skip" in plan["say"]
