"""REVERSAL engine — volatility-boundary fade (the binary-options archetype).

This is the mean-reversion counterpart to MOMENTUM. Instead of trading with
the trend, it fades an *exhausted extreme* of price, betting that a price
that poked through a volatility boundary with fading momentum will snap back
toward the mean within the fixed expiry window. That snap-back is exactly what
a 1-5 minute binary option rewards.

PUT setup (mirror for CALL):
  1. Price pierces / closes beyond the upper volatility band
     (Bollinger upper, or Keltner upper).
  2. Stochastic RSI %K crossed DOWN in overbought territory (>= stoch_ob),
     i.e. momentum is turning lower at the extreme.
  3. The decision candle closes bearish (close < open) — a reversal candle.

Confluence guards:
  - Refuse the fade if the trend is violently aligned against it (strong ADX).
  - Refuse a dead band (bands too tight = no room to snap back) and a price
    spike (news).
  - Optional Fisher Transform confirmation of an exhausted extreme.

All evaluated on COMPLETED bars only (non-repainting).

Design for backtesting (same as momentum): prepare_reversal computes all
indicator series once; evaluate_reversal_at evaluates any closed index.
"""
from __future__ import annotations

import math

from . import indicators as ta
from .configutil import get, CFG
from .registry import make_signal, Signal


def _coerce(series) -> list:
    return [float(v) for v in series]


def prepare_reversal(decision: tuple, cfg: dict = CFG) -> dict:
    """Compute all reversal indicator series once over the closed bars."""
    o, h, l, c, v = (_coerce(x) for x in decision)
    r = get(cfg, "reversal", {})
    srs_p = int(get(r, "stoch_rsi_period", 14))
    srs_k = int(get(r, "stoch_rsi_k", 3))
    srs_d = int(get(r, "stoch_rsi_d", 3))
    bb_p = int(get(r, "bb_period", 20))
    bb_std = float(get(r, "bb_std", 2.0))
    kelt_p = int(get(r, "keltner_period", 20))
    kelt_mult = float(get(r, "keltner_atr_mult", 2.0))
    atr_p = int(get(r, "atr_period", 14))
    band = get(r, "band", "bollinger")
    adx_p = int(get(get(cfg, "momentum", {}), "adx_period", 14))

    if band == "keltner":
        mid, up, lo = ta.keltner(c, h, l, kelt_p, kelt_mult, atr_p)
    else:
        mid, up, lo = ta.bollinger(c, bb_p, bb_std)
    s_k, s_d = ta.stoch_rsi(c, srs_p, srs_k, srs_d)
    adxv, pdi, mdi = ta.adx(h, l, c, adx_p)
    fisher = ta.fisher_transform(h, l, 9) if get(r, "use_fisher", True) else None

    return {
        "o": o, "h": h, "l": l, "c": c, "v": v,
        "band_mid": mid, "band_up": up, "band_lo": lo,
        "stochrsi_k": s_k, "stochrsi_d": s_d,
        "adx": adxv, "pdi": pdi, "mdi": mdi,
        "fisher": fisher,
        "cfg": cfg,
        "band": band,
        "adx_p": adx_p,
        "len": len(c),
    }


def evaluate_reversal_at(prep: dict, idx: int, symbol: str, ts: str = "",
                         expiry_hint: int = 2) -> Signal | None:
    """Evaluate REVERSAL at closed bar `idx` of a prepared series."""
    i = int(idx)
    if i < 0:
        i += prep["len"]
    cfg = prep["cfg"]
    r = get(cfg, "reversal", {})
    st_ob = float(get(r, "stoch_ob", 80))
    st_os = float(get(r, "stoch_os", 20))
    body_min = float(get(r, "body_min_pct", 0.0003))
    min_bw = float(get(r, "min_band_width_pct", 0.001))
    max_range = float(get(r, "max_range_pct", 0.0040))
    require_past = bool(get(r, "require_close_past_band", True))
    use_fisher = bool(get(r, "use_fisher", True))
    adx_cap = float(get(r, "adx_cap", 38))

    o, h, l, c = prep["o"], prep["h"], prep["l"], prep["c"]
    mid, up, lo = prep["band_mid"], prep["band_up"], prep["band_lo"]
    s_k, s_d = prep["stochrsi_k"], prep["stochrsi_d"]
    adxv, pdi, mdi = prep["adx"], prep["pdi"], prep["mdi"]
    fisher = prep["fisher"]

    if i < 1:
        return None
    price = float(c[i])

    up_i, lo_i, mid_i = float(up[i]), float(lo[i]), float(mid[i])
    if not math.isfinite(up_i) or not math.isfinite(lo_i):
        return None
    band_w = (up_i - lo_i) / price if price else 0.0
    if band_w < min_bw:
        return None

    above_band = c[i] > up_i if require_past else h[i] > up_i
    below_band = c[i] < lo_i if require_past else l[i] < lo_i
    prev_inside = float(lo[i - 1]) < float(up[i - 1]) and float(h[i - 1]) > float(lo[i - 1])

    k_now = float(s_k[i]) if not math.isnan(s_k[i]) else None
    k_prev = float(s_k[i - 1]) if not math.isnan(s_k[i - 1]) else None

    body = abs(c[i] - o[i])
    bar_range = h[i] - l[i]
    too_spike = bar_range > max_range * price
    weak_body = body < body_min * price

    a = float(adxv[i]) if not math.isnan(adxv[i]) else None
    p = float(pdi[i]) if not math.isnan(pdi[i]) else None
    m_ = float(mdi[i]) if not math.isnan(mdi[i]) else None
    strong_trend = a is not None and a >= adx_cap

    fisher_ok = True
    if use_fisher and fisher is not None and not math.isnan(fisher[i]):
        fisher_ok = float(fisher[i]) > 1.5 or float(fisher[i]) < -1.5

    indicators = {
        "band_mid": round(mid_i, 5),
        "band_up": round(up_i, 5),
        "band_lo": round(lo_i, 5),
        "band_width_pct": round(band_w, 5),
        "stochrsi_k": round(k_now, 1) if k_now is not None else None,
        "stochrsi_d": round(float(s_d[i]), 1) if not math.isnan(s_d[i]) else None,
        "adx": round(a, 1) if a is not None else None,
    }

    # ---------- PUT: fade the top ----------
    if above_band and body > 0 and c[i] < o[i]:
        cross_down = (k_now is not None and k_prev is not None
                      and k_prev >= st_ob and k_now < k_prev)
        if strong_trend or too_spike or weak_body or not cross_down or not fisher_ok:
            return None
        conf = 0.55
        conf += 0.10 * (1 if k_now >= st_ob else 0)
        conf += 0.10 * (1 if m_ is not None and (m_ or 0) > (p or 0) else 0)
        conf += 0.10 * (1 if body > 2 * body_min * price else 0)
        conf += 0.05 * (1 if prev_inside else 0)
        conf += 0.05 * (1 - min(1.0, (price - mid_i) / max(up_i - mid_i, 1e-12)))
        return make_signal(
            symbol, "PUT", "REVERSAL", conf, price, ts, expiry_hint,
            ["pierce upper band + StochRSI cross down + bearish close"], indicators,
        )

    # ---------- CALL: fade the bottom ----------
    if below_band and body > 0 and c[i] > o[i]:
        cross_up = (k_now is not None and k_prev is not None
                    and k_prev <= st_os and k_now > k_prev)
        if strong_trend or too_spike or weak_body or not cross_up or not fisher_ok:
            return None
        conf = 0.55
        conf += 0.10 * (1 if k_now <= st_os else 0)
        conf += 0.10 * (1 if p is not None and (p or 0) > (m_ or 0) else 0)
        conf += 0.10 * (1 if body > 2 * body_min * price else 0)
        conf += 0.05 * (1 if prev_inside else 0)
        conf += 0.05 * (1 - min(1.0, (mid_i - price) / max(mid_i - lo_i, 1e-12)))
        return make_signal(
            symbol, "CALL", "REVERSAL", conf, price, ts, expiry_hint,
            ["pierce lower band + StochRSI cross up + bullish close"], indicators,
        )

    return None


def evaluate_reversal(
    symbol: str, decision: tuple, ts: str = "",
    expiry_hint: int = 2, cfg: dict = CFG,
) -> Signal | None:
    """Live convenience: prepare once and evaluate the last closed bar."""
    prep = prepare_reversal(decision, cfg)
    return evaluate_reversal_at(prep, -1, symbol, ts, expiry_hint)
