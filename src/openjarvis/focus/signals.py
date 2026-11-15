"""Micro-futures position sizing and a simple trend signal.

Advisory only: nothing here places an order. Sizing is driven by the most
the trader is willing to lose on one trade, so a signal whose stop does not
fit inside that limit is reported as "wait", never rounded up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence


@dataclass(frozen=True)
class Contract:
    symbol: str
    name: str
    tick_size: float
    tick_value: float

    @property
    def point_value(self) -> float:
        return self.tick_value / self.tick_size


CONTRACTS: Dict[str, Contract] = {
    "MES": Contract("MES", "Micro E-mini S&P 500", 0.25, 1.25),
    "MNQ": Contract("MNQ", "Micro E-mini Nasdaq-100", 0.25, 0.50),
    "MYM": Contract("MYM", "Micro E-mini Dow", 1.0, 0.50),
    "M2K": Contract("M2K", "Micro E-mini Russell 2000", 0.10, 0.50),
}


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: str  # "long" | "short" | "wait"
    reason: str
    contracts: int = 0
    stop_points: float = 0.0
    risk_dollars: float = 0.0


def _contract(symbol: str) -> Contract:
    try:
        return CONTRACTS[symbol.upper()]
    except KeyError as exc:
        raise ValueError(f"Unknown contract: {symbol}") from exc


def max_stop_points(symbol: str, risk_dollars: float) -> float:
    """Widest stop, in points, one contract can carry inside the risk limit."""
    c = _contract(symbol)
    ticks = math.floor(risk_dollars / c.tick_value + 1e-9)
    return round(ticks * c.tick_size, 4)


def position_size(symbol: str, risk_dollars: float, stop_points: float) -> int:
    """Whole contracts whose loss at the stop stays within ``risk_dollars``."""
    if stop_points <= 0 or risk_dollars <= 0:
        return 0
    c = _contract(symbol)
    return math.floor(risk_dollars / (stop_points * c.point_value) + 1e-9)


def ema(values: Sequence[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def atr(bars: Sequence[Dict[str, float]], period: int = 14) -> float:
    """Average true range over the last ``period`` bars."""
    ranges = []
    for prev, bar in zip(bars, bars[1:]):
        ranges.append(
            max(
                bar["high"] - bar["low"],
                abs(bar["high"] - prev["close"]),
                abs(bar["low"] - prev["close"]),
            )
        )
    tail = ranges[-period:]
    return sum(tail) / len(tail) if tail else 0.0


def evaluate(
    symbol: str,
    bars: Sequence[Dict[str, float]],
    risk_dollars: float,
    *,
    fast: int = 9,
    slow: int = 21,
    atr_mult: float = 1.5,
) -> Signal:
    """Trend signal from an EMA crossover on the latest bar, sized by risk."""
    c = _contract(symbol)
    if len(bars) < slow + 2:
        return Signal(c.symbol, "wait", "Not enough price history yet")
    closes = [b["close"] for b in bars]
    f, s = ema(closes, fast), ema(closes, slow)
    was_above, is_above = f[-2] > s[-2], f[-1] > s[-1]
    if was_above == is_above:
        trend = "up" if is_above else "down"
        return Signal(c.symbol, "wait", f"Trend is {trend}, no fresh entry")
    raw_stop = atr(bars) * atr_mult
    ticks = max(1, math.ceil(raw_stop / c.tick_size - 1e-9))
    stop = round(ticks * c.tick_size, 4)
    contracts = position_size(c.symbol, risk_dollars, stop)
    if contracts == 0:
        need = stop * c.point_value
        return Signal(
            c.symbol,
            "wait",
            f"Entry signal, but the stop needs ${need:.2f} of risk per contract "
            f"and your limit is ${risk_dollars:.2f}",
            stop_points=stop,
        )
    action = "long" if is_above else "short"
    return Signal(
        c.symbol,
        action,
        f"Fast average crossed {'above' if is_above else 'below'} the slow average",
        contracts=contracts,
        stop_points=stop,
        risk_dollars=round(contracts * stop * c.point_value, 2),
    )


def normalize_symbol(raw: str) -> str:
    """Map chart tickers such as ``MES1!`` or ``MNQZ2026`` to a known root."""
    text = (raw or "").upper().split(":")[-1]
    for root in CONTRACTS:
        if text.startswith(root):
            return root
    raise ValueError(f"Unknown contract: {raw}")


def _round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 4)


def plan_trade(
    symbol: str,
    side: str,
    price: float,
    atr_points: float,
    risk_dollars: float,
    *,
    atr_mult: float = 1.5,
    reward_mult: float = 2.0,
) -> Dict[str, object]:
    """Turn an entry alert into contracts, stop, target and a trailing rule.

    The stop sits ``atr_mult`` average ranges from the entry. If one contract
    at that stop would lose more than ``risk_dollars`` the plan says "wait".
    """
    c = _contract(normalize_symbol(symbol))
    if side not in ("long", "short"):
        raise ValueError(f"Unknown side: {side}")
    ticks = max(1, math.ceil(atr_points * atr_mult / c.tick_size - 1e-9))
    stop_points = round(ticks * c.tick_size, 4)
    contracts = position_size(c.symbol, risk_dollars, stop_points)
    per_contract = round(stop_points * c.point_value, 2)
    plan: Dict[str, object] = {
        "symbol": c.symbol,
        "side": side,
        "price": price,
        "stop_points": stop_points,
        "contracts": contracts,
    }
    if contracts == 0:
        plan["action"] = "wait"
        plan["say"] = (
            f"{c.symbol} gave a {side} signal, but the stop needs "
            f"${per_contract:.2f} per contract and your limit is "
            f"${risk_dollars:.2f}. Skip this one."
        )
        return plan
    sign = 1 if side == "long" else -1
    stop = _round_to_tick(price - sign * stop_points, c.tick_size)
    target = _round_to_tick(price + sign * stop_points * reward_mult, c.tick_size)
    trail_at = _round_to_tick(price + sign * stop_points, c.tick_size)
    plan.update(
        action=side,
        stop=stop,
        target=target,
        trail_at=trail_at,
        risk_dollars=round(contracts * per_contract, 2),
        say=(
            f"{c.symbol}: go {side}, {contracts} contract"
            f"{'s' if contracts != 1 else ''} near {price:g}. Stop at {stop:g}, "
            f"target {target:g}. When price reaches {trail_at:g}, move your "
            f"stop to your entry and trail it from there."
        ),
    )
    return plan
