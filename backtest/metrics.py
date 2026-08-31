"""Backtest metrics — win rate, session stats, streak & expectancy analytics.

Turns a list of Trade objects into the numbers an operator (or paying
customer) actually cares about for fixed-time binary options:

  * overall + per-model + per-session win rate and signal count
  * max consecutive losses (binary options are all-or-nothing: streak risk)
  * expectancy at a configurable payout (stake = 1)
  * breakeven win rate (what you need at a given payout: 1/(1+payout))
  * Kelly fraction (fraction of bankroll per trade at the achieved edge)

Note: all history timestamps are ISO, session classification is by UTC hour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .runner import Trade


def _session_key(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        h = dt.hour
    except Exception:
        h = 0
    # London 07-15 UTC, New York 12-21 UTC, Asia 00-08 UTC (approx, overlapping fine)
    if 12 <= h < 21:
        return "NY (12-21Z)"
    if 7 <= h < 15:
        return "London (7-15Z)"
    if h < 8 or h >= 21:
        return "Asia/off (0-8Z / 21-24Z)"
    return "other"


@dataclass
class Metrics:
    symbol: str
    expiry_bars: int
    trades: list = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.win)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    def per_model(self) -> dict:
        out = {}
        for model in ("MOMENTUM", "REVERSAL"):
            ts = [t for t in self.trades if t.model == model]
            if not ts:
                continue
            wins = sum(1 for t in ts if t.win)
            out[model] = {"n": len(ts), "win_rate": wins / len(ts)}
        return out

    def per_session(self) -> dict:
        out = {}
        buckets: dict[str, list] = {}
        for t in self.trades:
            buckets.setdefault(_session_key(t.time), []).append(t)
        for sess, ts in buckets.items():
            wins = sum(1 for t in ts if t.win)
            out[sess] = {"n": len(ts), "win_rate": wins / len(ts)}
        return out

    def per_side(self) -> dict:
        out = {}
        for side in ("CALL", "PUT"):
            ts = [t for t in self.trades if t.side == side]
            if not ts:
                continue
            wins = sum(1 for t in ts if t.win)
            out[side] = {"n": len(ts), "win_rate": wins / len(ts)}
        return out

    def max_consecutive_losses(self) -> int:
        worst = cur = 0
        for t in self.trades:
            if t.win:
                cur = 0
            else:
                cur += 1
                worst = max(worst, cur)
        return worst

    def max_consecutive_wins(self) -> int:
        worst = cur = 0
        for t in self.trades:
            if t.win:
                cur += 1
                worst = max(worst, cur)
            else:
                cur = 0
        return worst

    def breakeven_win_rate(self, payout: float) -> float:
        """Win rate needed to break even at a given binary payout ratio."""
        if payout <= 0:
            return 1.0
        return 1.0 / (1.0 + payout)

    def expectancy(self, payout: float, stake: float = 1.0) -> float:
        """Return per-trade expected P/L in units of `stake`."""
        return stake * (self.win_rate * payout - (1 - self.win_rate))

    def kelly_fraction(self, payout: float) -> float:
        """Fraction of bankroll to risk per trade at the achieved edge."""
        if self.n == 0 or self.win_rate == 0:
            return 0.0
        # f* = (p*b - q) / b, with b=payout, p=win_rate, q=1-p
        b = payout
        f = (self.win_rate * b - (1 - self.win_rate)) / b
        return max(0.0, f)

    def report(self, payout: float, stake: float = 1.0) -> str:
        lines = []
        lines.append(f"  Symbol {self.symbol}   expiry={self.expiry_bars} bar(s)")
        lines.append(f"  Signals   : {self.n}")
        if self.n == 0:
            lines.append("  (no signals fired in this window / with these filters)")
            return "\n".join(lines)
        lines.append(f"  Win rate  : {self.win_rate:.1%}  ({self.wins}/{self.n})  target>60%: "
                     f"{'YES' if self.win_rate > 0.60 else 'NO'}")
        lines.append(f"  Breakeven : {self.breakeven_win_rate(payout):.1%}  at payout {payout:.2f}")
        lines.append(f"  Expectancy: {self.expectancy(payout, stake):+.3f} per {stake:.2f} stake "
                     f"({'PROFITABLE' if self.expectancy(payout, stake) > 0 else 'LOSS'})")
        lines.append(f"  Kelly     : {self.kelly_fraction(payout):.1%} of bankroll/trade")
        lines.append(f"  Max cons. : {self.max_consecutive_losses()} losses / "
                     f"{self.max_consecutive_wins()} wins")
        pm = self.per_model()
        if pm:
            lines.append("  By model  : " + ", ".join(
                f"{m}={v['n']} ({v['win_rate']:.0%})" for m, v in pm.items()))
        ps = self.per_session()
        if ps:
            lines.append("  By session: " + ", ".join(
                f"{s}={v['n']} ({v['win_rate']:.0%})" for s, v in ps.items()))
        pside = self.per_side()
        if pside:
            lines.append("  By side   : " + ", ".join(
                f"{s}={v['n']} ({v['win_rate']:.0%})" for s, v in pside.items()))
        return "\n".join(lines)
