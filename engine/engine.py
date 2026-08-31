"""Engine orchestrator — runs the configured model(s) for one instrument.

Bridges the OANDA data layer to the momentum/reversal engines. Fetches the
decision-timeframe candles (and, for MOMENTUM, upper-timeframe series), builds
closed-bar numpy arrays with timestamps, and returns Signal objects for the
most recent closed decision bar (typically zero or one).

Non-repainting: only ``complete`` candles are used (handled in
oanda.candle_rows), and engines only read indices <= the last closed bar.
"""
from __future__ import annotations

from . import oanda
from .configutil import CFG, get, load_watchlist
from .registry import Signal
from .momentum import prepare_momentum, evaluate_momentum_at
from .reversal import prepare_reversal, evaluate_reversal_at


def _upper_series(symbol: str, tf: str) -> tuple:
    """Fetch an upper-timeframe series best-effort; empty lists on failure."""
    try:
        rows = oanda.fetch_candles(symbol, tf, 300)
        return oanda.to_arrays(rows)
    except Exception:
        return ([], [], [], [], [])


def analyze_symbol(symbol: str, cfg: dict = CFG) -> list[Signal]:
    """Run the configured model(s) on the last closed decision bar of `symbol`."""
    from .configutil import load_watchlist as _wl

    tf_dec = get(cfg, "tf_decision", "M1")
    count = max(int(get(cfg, "min_bars", 300)), 300)
    rows = oanda.fetch_candles(symbol, tf_dec, count)
    if len(rows) < 60:
        return []

    arrays = oanda.to_arrays(rows)
    ts_list = [r["t"] for r in rows]

    hint = 5
    for entry in _wl():
        if entry.get("symbol") == symbol and entry.get("expiry_hint"):
            hint = int(entry["expiry_hint"])

    model = get(cfg, "model", "both")
    signals = []

    if model in ("both", "REVERSAL", "reversal"):
        prep = prepare_reversal(arrays, cfg)
        sig = evaluate_reversal_at(prep, -1, symbol, ts_list[-1], hint)
        if sig:
            signals.append(sig)
    if model in ("both", "MOMENTUM", "momentum"):
        mt = get(cfg, "momentum", {})
        confirm = _upper_series(symbol, get(mt, "tf_trend", "M5"))
        major = _upper_series(symbol, get(mt, "tf_major", "M15"))
        prep = prepare_momentum(arrays, confirm, major, cfg)
        sig = evaluate_momentum_at(prep, -1, symbol, ts_list[-1], hint)
        if sig:
            signals.append(sig)

    return signals
