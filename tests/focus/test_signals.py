import pytest

from openjarvis.focus.signals import (
    CONTRACTS,
    evaluate,
    max_stop_points,
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
