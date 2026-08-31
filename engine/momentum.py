"""MOMENTUM engine — trend-continuation (v1 logic, vectorized).

Philosophy: only trade in the direction a strong multi-timeframe trend, at a
fresh pullback where momentum is re-accelerating. The default is NO_TRADE;
signals are rare but high-confluence.

Confluence stack (independent filters):
  1. Multi-timeframe trend agreement (M1 vs M5 vs M15 bias).
  2. Trend strength (ADX) + directional movement (DI).
  3. RSI refusal of overbought CALLs / oversold PUTs.
  4. Stochastic momentum fresh turn in the trend direction.
  5. Pullback structure (price retraced to the fast EMA, not chasing).
  6. Bar-quality guards (refuse dead bars and news spikes).

All conditions are evaluated on COMPLETED bars only (non-repainting).

Design for backtesting: ``prepare_momentum`` computes every indicator ONCE over
the full closed series; ``evaluate_momentum_at`` then evaluates the decision at
any index without recomputation. Live use calls prepare + evaluate at idx=-1.
"""
from __future__ import annotations

import math

from . import indicators as ta
from .configutil import get, CFG
from .registry import make_signal, Signal


def _coerce(series) -> list:
    return [float(v) for v in series]


def _trend_bias(closes, fast_p: int, slow_p: int) -> int:
    """+1 fast>slow, -1 fast<slow, 0 else (with graceful slow shrink)."""
    if len(closes) < fast_p + 3:
        return 0
    slow = min(slow_p, max(fast_p + 2, 20))
    ef = ta.ema(closes, fast_p)[-1]
    es = ta.ema(closes, slow)[-1]
    if not math.isnan(ef) and not math.isnan(es):
        if ef > es:
            return 1
        if ef < es:
            return -1
    return 0


def prepare_momentum(
    decision: tuple, confirm: tuple, major: tuple, cfg: dict = CFG,
) -> dict:
    """Compute all indicator series once. Returns a dict the evaluator consumes."""
    o, h, l, c, v = (_coerce(x) for x in decision)
    co, ch, cl, cc, cv = (_coerce(x) for x in confirm)
    mo, mh, ml, mc, mv = (_coerce(x) for x in major)

    m = get(cfg, "momentum", {})
    ema_fast = int(get(m, "ema_fast", 50))
    ema_slow = int(get(m, "ema_slow", 200))
    rsi_p = int(get(m, "rsi_period", 14))
    adx_p = int(get(m, "adx_period", 14))
    sk = int(get(m, "stoch_k", 14))
    sd = int(get(m, "stoch_d", 3))

    return {
        "o": o, "h": h, "l": l, "c": c, "v": v,
        "cc": cc, "mc": mc,
        "ema_fast": ta.ema(c, ema_fast),
        "ema_slow": ta.ema(c, ema_slow),
        "rsi": ta.rsi(c, rsi_p),
        "stoch": ta.stochastic(h, l, c, sk, sd),   # (k, d)
        "adx": ta.adx(h, l, c, adx_p),             # (adx, +DI, -DI)
        "ema_fast_p": ema_fast,
        "ema_slow_p": ema_slow,
        "rsi_p": rsi_p,
        "adx_p": adx_p,
        "sk": sk,
        "sd": sd,
        "cfg": cfg,
        "len": len(c),
    }


def evaluate_momentum_at(prep: dict, idx: int, symbol: str, ts: str = "",
                         expiry_hint: int = 5) -> Signal | None:
    """Evaluate the MOMENTUM decision at closed bar `idx` of the prepared series."""
    i = int(idx)
    if i < 0:
        i += prep["len"]
    cfg = prep["cfg"]
    m = get(cfg, "momentum", {})
    ema_fast = prep["ema_fast_p"]
    ema_slow = prep["ema_slow_p"]
    rsi_ob = float(get(m, "rsi_ob", 70))
    rsi_os = float(get(m, "rsi_os", 30))
    adx_min = float(get(m, "adx_min", 22))
    di_min = float(get(m, "di_min", 15))
    st_ob = float(get(m, "stoch_ob", 80))
    st_os = float(get(m, "stoch_os", 20))
    pullback_pct = float(get(m, "pullback_pct", 0.0012))
    min_range = float(get(m, "min_range_pct", 0.00020))
    max_range = float(get(m, "max_range_pct", 0.0040))

    o, h, l, c = prep["o"], prep["h"], prep["l"], prep["c"]
    cc, mc = prep["cc"], prep["mc"]
    ef_series, es_series = prep["ema_fast"], prep["ema_slow"]
    rsi_s, (k, d) = prep["rsi"], prep["stoch"]
    adxv, pdi, mdi = prep["adx"]

    if i < 1:
        return None
    price = float(c[i])
    ef = float(ef_series[i])
    es = float(es_series[i])
    if math.isnan(ef) or math.isnan(es):
        return None

    # 1. MTF trend agreement.
    bias1 = 1 if ef > es else (-1 if ef < es else 0)
    biasC = _trend_bias(cc, ema_fast, 60) if len(cc) >= 20 else 0
    biasM = _trend_bias(mc, ema_fast, 100) if len(mc) >= 20 else 0
    aligned = bias1 != 0 and biasC == bias1 and biasM == bias1

    # 2. ADX / DI strength.
    a = float(adxv[i]) if not math.isnan(adxv[i]) else None
    p = float(pdi[i]) if not math.isnan(pdi[i]) else None
    m_ = float(mdi[i]) if not math.isnan(mdi[i]) else None
    strong = a is not None and a >= adx_min and max(p or 0, m_ or 0) >= di_min
    up_strength = (p or 0) > (m_ or 0)

    # 3. RSI.
    r = float(rsi_s[i]) if not math.isnan(rsi_s[i]) else None

    # 4. Stochastic.
    kk = float(k[i]) if not math.isnan(k[i]) else None
    dd = float(d[i]) if not math.isnan(d[i]) else None

    # 5. Pullback structure.
    tol = pullback_pct * price
    pullup = (l[i] <= ef + tol) and (c[i] <= ef + 2 * tol)
    pulldn = (h[i] >= ef - tol) and (c[i] >= ef - 2 * tol)

    # 6. Bar quality.
    bar_range = h[i] - l[i]
    too_dead = bar_range < min_range * price
    too_spike = bar_range > max_range * price

    if not (aligned and strong and not too_dead and not too_spike):
        return None

    indicators = {
        "rsi": round(r, 1) if r is not None else None,
        "stoch_k": round(kk, 1) if kk is not None else None,
        "stoch_d": round(dd, 1) if dd is not None else None,
        "adx": round(a, 1) if a is not None else None,
        "di_plus": round(p, 1) if p is not None else None,
        "di_minus": round(m_, 1) if m_ is not None else None,
        "ema_fast": round(ef, 5),
        "ema_slow": round(es, 5),
        "bias1": bias1,
        "biasC": biasC,
        "biasM": biasM,
    }

    if bias1 == 1 and up_strength and pullup:
        if r is not None and r >= rsi_ob:
            return None
        if kk is not None and (kk >= st_ob or (dd or 0) >= st_ob):
            return None
        conf = 0.55
        conf += 0.10 * (1 if a is not None and a >= adx_min + 4 else 0)
        conf += 0.10 * (1 if biasC == 1 and biasM == 1 else 0)
        conf += 0.10 * (1 if p is not None and (p or 0) > (m_ or 0) + 8 else 0)
        conf += 0.05 * (1 if dd is not None and dd < 45 else 0)
        conf += 0.10 * (1 - min(1.0, (ef - price) / (tol * 4 + 1e-12)))
        return make_signal(
            symbol, "CALL", "MOMENTUM", conf, price, ts,
            expiry_hint, ["MTF uptrend + fresh pullback"], indicators,
        )

    if bias1 == -1 and not up_strength and pulldn:
        if r is not None and r <= rsi_os:
            return None
        if kk is not None and (kk <= st_os or (dd or 0) <= st_os):
            return None
        conf = 0.55
        conf += 0.10 * (1 if a is not None and a >= adx_min + 4 else 0)
        conf += 0.10 * (1 if biasC == -1 and biasM == -1 else 0)
        conf += 0.10 * (1 if m_ is not None and (m_ or 0) > (p or 0) + 8 else 0)
        conf += 0.05 * (1 if dd is not None and dd > 55 else 0)
        conf += 0.10 * (1 - min(1.0, (price - es) / (tol * 4 + 1e-12)))
        return make_signal(
            symbol, "PUT", "MOMENTUM", conf, price, ts,
            expiry_hint, ["MTF downtrend + fresh rally"], indicators,
        )

    return None


def evaluate_momentum(
    symbol: str,
    decision: tuple, confirm: tuple, major: tuple,
    ts: str = "", expiry_hint: int = 5, cfg: dict = CFG,
) -> Signal | None:
    """Live convenience: prepare once and evaluate the last closed bar."""
    prep = prepare_momentum(decision, confirm, major, cfg)
    return evaluate_momentum_at(prep, -1, symbol, ts, expiry_hint)
