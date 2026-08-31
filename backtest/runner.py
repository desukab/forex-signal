"""Backtest runner — replay signals on closed bars and score binary outcomes.

This is the heart of the "sellable" claim: it proves (or disproves) a config's
win rate on real historical OANDA data. Discipline guarantees:

  * Signal at bar i uses ONLY data up to and including closed bar i (no lookahead).
  * Outcome at expiry uses ONLY the close of bar i+N (N = expiry length).
  * Only ``complete`` candles are ever used (non-repainting).
  * Expectancy is computed at a configurable binary payout, so the 60% target
    can be checked: at payout=P, break-even win rate is 1/(1+P).

Because a binary option at fixed expiry is decided by price being up/down at
that moment (an all-or-nothing market-entry approximation), we score:
    CALL win  <=> close[i+N] >  close[i]
    PUT  win  <=> close[i+N] <  close[i]
Account for the broker spread / payout when interpreting results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from engine import oanda
from engine.configutil import CFG, get
from engine.registry import Signal
from engine.momentum import prepare_momentum, evaluate_momentum_at
from engine.reversal import prepare_reversal, evaluate_reversal_at


@dataclass
class Trade:
    """One replayed signal + its realised outcome at a chosen expiry."""
    symbol: str
    side: str
    model: str
    time: str
    price: float
    expiry_bars: int
    close_future: float
    win: bool
    confidence: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "side": self.side, "model": self.model,
            "time": self.time, "price": self.price,
            "expiry_bars": self.expiry_bars, "close_future": self.close_future,
            "win": self.win, "confidence": round(self.confidence, 3),
        }


def _resolve_time(rows: list[dict], i: int) -> str:
    if i < len(rows):
        return rows[i]["t"]
    return ""


def replay_symbol(
    symbol: str,
    expiry_bars: int,
    model: str = "both",
    cfg: dict = CFG,
    days_back: int | None = None,
) -> list[Trade]:
    """Replay every closed decision bar for `symbol` over the history window.

    Returns a flat list of Trades (one per signal fired) with realised outcomes
    at `expiry_bars` decision-bars ahead. Never looks ahead when signalling.
    """
    bt = get(cfg, "backtest", {})
    days = days_back if days_back is not None else int(get(bt, "days_back", 30))
    warmup = int(get(bt, "warmup_bars", 250))
    tf_dec = get(cfg, "tf_decision", "M1")

    rows = oanda.fetch_history(symbol, tf_dec, days)
    if len(rows) < warmup + expiry_bars + 40:
        # not enough history to warm indicators and leave expiry room
        return []

    arrays = oanda.to_arrays(rows)
    ts_list = [r["t"] for r in rows]
    bets = get(cfg, "momentum", {})

    trades: list[Trade] = []
    # Prepare once; evaluate each closed bar in sequence.
    prep_rev = None
    prep_mom = None
    if model in ("both", "REVERSAL", "reversal"):
        prep_rev = prepare_reversal(arrays, cfg)
    if model in ("both", "MOMENTUM", "momentum"):
        confirm = _upper_series(symbol, get(bets, "tf_trend", "M5"))
        major = _upper_series(symbol, get(bets, "tf_major", "M15"))
        prep_mom = prepare_momentum(arrays, confirm, major, cfg)

    last = len(rows) - expiry_bars - 1  # need i+expiry_bars to exist
    for i in range(warmup, last + 1):
        # --- momentum
        if prep_mom is not None:
            sig = evaluate_momentum_at(prep_mom, i, symbol, ts_list[i], _hint(cfg, symbol))
            if sig:
                trades.append(_score(sig, rows, i, expiry_bars))
        # --- reversal
        if prep_rev is not None:
            sig = evaluate_reversal_at(prep_rev, i, symbol, ts_list[i], _hint(cfg, symbol))
            if sig:
                trades.append(_score(sig, rows, i, expiry_bars))
    return trades


def _hint(cfg, symbol) -> int:
    from engine.configutil import load_watchlist
    for entry in load_watchlist():
        if entry.get("symbol") == symbol and entry.get("expiry_hint"):
            return int(entry["expiry_hint"])
    return 5


def _upper_series(symbol: str, tf: str):
    try:
        rows = oanda.fetch_candles(symbol, tf, 400)
        return oanda.to_arrays(rows)
    except Exception:
        return ([], [], [], [], [])


def _score(sig: Signal, rows: list[dict], i: int, expiry_bars: int) -> Trade:
    """Resolve the outcome of a signal fired at bar i after `expiry_bars`."""
    j = i + expiry_bars
    close_at_entry = sig.price
    close_future = float(rows[j]["c"]) if j < len(rows) else close_at_entry
    if sig.side == "CALL":
        win = close_future > close_at_entry
    else:
        win = close_future < close_at_entry
    return Trade(
        symbol=sig.symbol, side=sig.side, model=sig.model,
        time=sig.time, price=close_at_entry, expiry_bars=expiry_bars,
        close_future=close_future, win=win, confidence=sig.confidence,
    )
