"""Unified signal model + confidence scoring for the v2 engine.

Both engines (momentum, reversal) return a plain dict through this registry so
the backtester, notifier and CLI consume one shape. Confidence is a 0..1 score
used for display and (optionally) as a trade filter; it is heuristic, not a
probability guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Signal:
    symbol: str
    side: str                # "CALL" | "PUT"
    model: str               # "MOMENTUM" | "REVERSAL"
    confidence: float        # 0..1 heuristic score
    price: float             # decision-candle close (mid)
    time: str                # RFC3339 close timestamp of the expired candle
    expiry_hint: int         # suggested minutes for the fixed-time expiry
    reasons: list = field(default_factory=list)
    indicators: dict = field(default_factory=dict)
    expires_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), indent=2)

    def short(self) -> str:
        return (f"[{self.model}] {self.side} conf={self.confidence:.0%} "
                f"@ {self.price} expiry~{self.expiry_hint}m")


def make_signal(
    symbol: str, side: str, model: str, confidence: float,
    price: float, time: str, expiry_hint: int,
    reasons: list, indicators: dict,
) -> Signal:
    return Signal(
        symbol=symbol,
        side=side,
        model=model,
        confidence=max(0.0, min(confidence, 1.0)),
        price=float(price),
        time=time,
        expiry_hint=max(1, int(expiry_hint)),
        reasons=list(reasons),
        indicators=dict(indicators),
    )
