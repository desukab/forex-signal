"""Expiry optimizer — find the best fixed-expiry length per pair/model.

For a given symbol, replay the full history once and score outcomes at every
candidate expiry length (default M1..M5 decision bars). Reports a table so the
operator can pick the expiry with the best measured win rate — the single most
load-bearing parameter in fixed-time binary options.
"""
from __future__ import annotations

from engine.configutil import CFG, get
from .runner import replay_symbol
from .metrics import Metrics


def optimize_symbol(
    symbol: str,
    expiries: list[int] | None = None,
    model: str = "both",
    cfg: dict = CFG,
    days_back: int | None = None,
) -> dict:
    """Return {expiry_bars: Metrics} for each candidate expiry length."""
    bt = get(cfg, "backtest", {})
    if expiries is None:
        expiries = list(get(bt, "expiries", [1, 2, 3, 5]))
    out = {}
    for n in sorted(set(expiries)):
        trades = replay_symbol(symbol, n, model=model, cfg=cfg, days_back=days_back)
        out[n] = Metrics(symbol=symbol, expiry_bars=n, trades=trades)
    return out


def best_expiry(results: dict, min_signals: int = 5) -> tuple[int, Metrics] | None:
    """Pick the expiry with the best win rate among those with enough signals."""
    cands = [(n, m) for n, m in results.items() if m.n >= min_signals]
    if not cands:
        return None
    best = max(cands, key=lambda nm: nm[1].win_rate)
    return best


def optimize_report(symbol: str, model: str = "both", cfg: dict = CFG,
                    days_back: int | None = None) -> str:
    bt = get(cfg, "backtest", {})
    min_signals = int(get(bt, "min_signals", 20))
    payout = float(get(bt, "payout", 0.8))
    results = optimize_symbol(symbol, model=model, cfg=cfg, days_back=days_back)

    lines = [f"=== Expiry optimizer: {symbol.replace('_','/')} "
             f"(model={model}) ==="]
    header = f"  {'Expiry':<8}{'Signals':<9}{'WinRate':<9}{'BeWin':<8}{'Expect':<8}Meets 60%?"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    met = []
    for n in sorted(results):
        m = results[n]
        if m.n == 0:
            lines.append(f"  {n:<8}{0:<9}{'-':<9}{'-':<8}{'-':<8}no signals")
            b = None
        else:
            bw = m.breakeven_win_rate(payout)
            exp = m.expectancy(payout, 1.0)
            ok = "YES" if m.win_rate > 0.60 and m.n >= min_signals else "no"
            lines.append(
                f"  {n:<8}{m.n:<9}{m.win_rate:<9.1%}{bw:<8.1%}{exp:<+8.3f}{ok}")
            if ok == "YES":
                met.append((n, m))

    best = best_expiry(results, min_signals=min_signals)
    if best:
        n, m = best
        lines.append("")
        lines.append(f"  Best expiry: {n} bar(s)  ->  win rate {m.win_rate:.1%} "
                     f"({m.wins}/{m.n})")
        lines.append(m.report(payout))
    else:
        lines.append("")
        lines.append("  No expiry met >60% with enough signals. Try looser filters,")
        lines.append("  more history, or a different pair.")
    return "\n".join(lines)
