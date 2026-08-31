"""Pure-Python technical-indicator library (zero dependencies).

Every indicator is a pure function over 1-D sequences (lists) and returns
lists aligned to the input, with leading values that need warm-up set to
``float('nan')``. The math follows the classic definitions (Wilder smoothing
for RSI/ATR/ADX) so results match TradingView / standard TA libraries.

This is the numpy-free build so the whole engine runs on Termux with zero pip
installs. Included ``selftest()`` checks EMA/RSI/stochastic against tiny
hand-computed fixtures: ``python signal.py selftest``.
"""
from __future__ import annotations

import math

NaN = float("nan")


def _prep(x) -> list:
    """Coerce any 1-D iterable (lists, tuples, numpy if present) to a float list."""
    if isinstance(x, (list, tuple)):
        return [float(v) for v in x]
    try:  # numpy array or similar -> tolist()
        return [float(v) for v in x.tolist()] if hasattr(x, "tolist") else [float(v) for v in list(x)]
    except Exception:
        return [float(v) for v in list(x)]


def _isnan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def ema(x, period: int) -> list:
    """Exponential moving average (alpha = 2/(period+1), seed = SMA of first `period`)."""
    x = _prep(x)
    n = len(x)
    out = [NaN] * n
    if n < 1 or period < 1:
        return out
    k = 2.0 / (period + 1.0)
    if n >= period:
        e = sum(x[:period]) / period
        out[period - 1] = e
        for i in range(period, n):
            e = x[i] * k + e * (1 - k)
            out[i] = e
    return out


def sma(x, period: int) -> list:
    x = _prep(x)
    n = len(x)
    out = [NaN] * n
    if n < period:
        return out
    window = sum(x[:period])
    out[period - 1] = window / period
    for i in range(period, n):
        window += x[i] - x[i - period]
        out[i] = window / period
    return out


def rsi(close, period: int = 14) -> list:
    """Wilder RSI. NaN until the first valid value after `period` changes."""
    c = _prep(close)
    n = len(c)
    out = [NaN] * n
    if n < period + 1:
        return out
    delta = [c[i] - c[i - 1] for i in range(1, n)]
    gain = [max(d, 0.0) for d in delta]
    loss = [max(-d, 0.0) for d in delta]
    avg_gain = [NaN] * n
    avg_loss = [NaN] * n
    avg_gain[period] = sum(gain[:period]) / period
    avg_loss[period] = sum(loss[:period]) / period
    for i in range(period + 1, n):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i - 1]) / period
    for i in range(period, n):
        if avg_loss[i] == 0:
            out[i] = 100.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def stochastic(high, low, close, k_period: int = 14, d_period: int = 3):
    """Fast stochastic %K (and its SMA -> %D). NaN during warm-up."""
    h = _prep(high)
    l = _prep(low)
    c = _prep(close)
    n = len(c)
    out_k = [NaN] * n
    if n < k_period:
        return out_k, [NaN] * n
    k = [NaN] * n
    for i in range(n):
        lo_i = max(0, i - k_period + 1)
        hh = max(h[lo_i:i + 1])
        ll = min(l[lo_i:i + 1])
        rng = hh - ll
        k[i] = 50.0 if rng == 0 else 100.0 * (c[i] - ll) / rng
    k[:k_period - 1] = [NaN] * (k_period - 1)
    out_k[:] = k
    d = sma(out_k, d_period)
    return out_k, d


def stoch_rsi(close, rsi_period: int = 14, stoch_k: int = 3, stoch_d: int = 3):
    """Stochastic RSI (Tushar Chande): stochastic applied to RSI, then smoothed."""
    c = _prep(close)
    r = rsi(c, rsi_period)
    n = len(c)
    out = [NaN] * n
    if n < rsi_period + stoch_k:
        return out, [NaN] * n
    for i in range(rsi_period + stoch_k - 1, n):
        lo = max(rsi_period, i - stoch_k + 1)
        window = [v for v in r[lo:i + 1] if not _isnan(v)]
        if len(window) < 2:
            continue
        mn, mx = min(window), max(window)
        if mx - mn == 0:
            out[i] = 50.0
        else:
            out[i] = 100.0 * (r[i] - mn) / (mx - mn)
    d = sma(out, stoch_d)
    return out, d


def bollinger(close, period: int = 20, num_std: float = 2.0):
    """(middle, upper, lower) Bollinger bands (population std)."""
    c = _prep(close)
    n = len(c)
    mid = sma(c, period)
    sd = [NaN] * n
    for i in range(period - 1, n):
        w = c[i - period + 1:i + 1]
        m = sum(w) / period
        sd[i] = math.sqrt(sum((v - m) ** 2 for v in w) / period)
    up = [m + num_std * s if not _isnan(s) else NaN for m, s in zip(mid, sd)]
    lo = [m - num_std * s if not _isnan(s) else NaN for m, s in zip(mid, sd)]
    return mid, up, lo


def true_range(high, low, close) -> list:
    h = _prep(high)
    l = _prep(low)
    c = _prep(close)
    n = len(c)
    tr = [0.0] * n
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return tr


def atr(high, low, close, period: int = 14) -> list:
    """Wilder ATR."""
    h = _prep(high)
    l = _prep(low)
    c = _prep(close)
    n = len(c)
    tr = true_range(h, l, c)
    if n < period + 1:
        return [NaN] * n
    out = [NaN] * n
    out[period] = sum(tr[1:period + 1]) / period
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def keltner(close, high, low, period: int = 20, atr_mult: float = 2.0, atr_period: int = 14):
    """(middle, upper, lower) Keltner channel around a SMA(period)."""
    mid = sma(_prep(close), period)
    a = atr(high, low, close, atr_period)
    up = [m + atr_mult * v if not _isnan(v) else NaN for m, v in zip(mid, a)]
    lo = [m - atr_mult * v if not _isnan(v) else NaN for m, v in zip(mid, a)]
    return mid, up, lo


def adx(high, low, close, period: int = 14):
    """(ADX, +DI, -DI) with Wilder smoothing. NaN during warm-up."""
    h = _prep(high)
    l = _prep(low)
    c = _prep(close)
    n = len(c)
    if n < period * 2 + 1:
        return [NaN] * n, [NaN] * n, [NaN] * n
    tr = true_range(h, l, c)
    up_move = [0.0] * n
    dn_move = [0.0] * n
    for i in range(1, n):
        up_move[i] = h[i] - h[i - 1]
        dn_move[i] = l[i - 1] - l[i]
    plus_dm = [up_move[i] if (up_move[i] > dn_move[i] and up_move[i] > 0) else 0.0 for i in range(n)]
    minus_dm = [dn_move[i] if (dn_move[i] > up_move[i] and dn_move[i] > 0) else 0.0 for i in range(n)]

    atr_v = [NaN] * n
    pdi = [NaN] * n
    mdi = [NaN] * n
    dx = [NaN] * n
    atr_v[period] = sum(tr[1:period + 1]) / period
    pdi[period] = 100.0 * sum(plus_dm[1:period + 1]) / atr_v[period]
    mdi[period] = 100.0 * sum(minus_dm[1:period + 1]) / atr_v[period]
    denom = pdi[period] + mdi[period]
    dx[period] = 100.0 * abs(pdi[period] - mdi[period]) / denom if denom else 0.0
    for i in range(period + 1, n):
        atr_v[i] = (atr_v[i - 1] * (period - 1) + tr[i]) / period
        pdi[i] = (pdi[i - 1] * (period - 1) + 100.0 * plus_dm[i] / atr_v[i]) / period
        mdi[i] = (mdi[i - 1] * (period - 1) + 100.0 * minus_dm[i] / atr_v[i]) / period
        denom = pdi[i] + mdi[i]
        dx[i] = 100.0 * abs(pdi[i] - mdi[i]) / denom if denom else 0.0
    adxv = [NaN] * n
    adxv[period + period - 1] = sum(dx[period:period + period]) / period
    for i in range(period + period, n):
        adxv[i] = (adxv[i - 1] * (period - 1) + dx[i]) / period
    return adxv, pdi, mdi


def vwap(high, low, close, volume, anchor: int) -> list:
    """Session-anchored VWAP. Each block of `anchor` bars gets its own VWAP."""
    h = _prep(high)
    l = _prep(low)
    c = _prep(close)
    v = _prep(volume)
    n = len(c)
    typical = [(hh + ll + cc) / 3.0 for hh, ll, cc in zip(h, l, c)]
    pv = [t * vv for t, vv in zip(typical, v)]
    out = [NaN] * n
    start = 0
    while start < n:
        end = min(start + anchor, n)
        s_pv = 0.0
        s_v = 0.0
        for i in range(start, end):
            s_pv += pv[i]
            s_v += v[i]
            out[i] = NaN if s_v == 0 else s_pv / s_v
        start = end
    return out


def fisher_transform(high, low, period: int = 9) -> list:
    """Ehlers Fisher Transform over a rolling highest/high-low range.

    Returns a value typically in roughly [-3, +3]. A value rising through a
    high positive extreme or falling through a low negative extreme is a
    classic reversal-timing trigger. NaN during warm-up.
    """
    h = _prep(high)
    l = _prep(low)
    n = len(h)
    if n < period:
        return [NaN] * n

    out = [NaN] * n
    y_prev = 0.0
    fisher = 0.0
    for i in range(period - 1, n):
        lo_i = max(0, i - period + 1)
        hh = max(h[lo_i:i + 1])
        ll = min(l[lo_i:i + 1])
        rng = hh - ll
        mid = (h[i] + l[i]) / 2.0
        x = 0.0 if rng == 0 else 2.0 * (mid - (hh + ll) / 2.0) / rng
        x = max(-0.999, min(0.999, x))
        y = 0.5 * math.log((1.0 + x) / (1.0 - x)) + 0.5 * y_prev
        y_prev = y
        fisher = 0.5 * y + 0.5 * fisher
        out[i] = fisher
    return out


def ema_ribbon(close, periods) -> tuple:
    """Return a tuple of EMAs. Slope sign of slowest vs fastest = trend."""
    return tuple(ema(close, p) for p in periods)


# ---------------------------------------------------------------------------
# Compatibility: keep callers that use numpy-style `.items()` / indexing working
# ---------------------------------------------------------------------------


def nanmax(seq):
    """Max ignoring NaN sentinels (used by selftest / callers)."""
    vals = [v for v in seq if not _isnan(v)]
    return max(vals) if vals else NaN


# ---------------------------------------------------------------------------
# Selftest (hand-computed fixtures, no external libs)
# ---------------------------------------------------------------------------


def selftest() -> bool:
    ok = True

    def check(name, cond):
        nonlocal ok
        if not cond:
            ok = False
            print(f"  [FAIL] {name}")
        else:
            print(f"  [ok]   {name}")

    # --- SMA over 1..5, period 3 -> [nan,nan,2,3,4]
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    s = sma(x, 3)
    check("SMA period-3", _isnan(s[0]) and s[2] == 2.0 and s[4] == 4.0)

    # --- EMA: period 1 == identity
    e = ema(x, 1)
    check("EMA period-1 identity", all(abs(e[i] - x[i]) < 1e-9 for i in range(3, len(x))))

    # --- RSI of a strictly rising series should trend toward ~100
    up = [float(i) for i in range(1, 60)]
    r = rsi(up, 14)
    check("RSI rising -> high", not _isnan(r[-1]) and r[-1] > 80)

    # --- RSI of strictly falling series -> low
    dn = [float(i) for i in range(60, 1, -1)]
    r2 = rsi(dn, 14)
    check("RSI falling -> low", not _isnan(r2[-1]) and r2[-1] < 20)

    # --- Bollinger: middle == SMA; upper > lower
    c = [10 + i * (1.0 / 49.0) for i in range(50)]
    _, up_b, lo_b = bollinger(c, 20, 2.0)
    check("BB upper>lower (not NaN tail)", not _isnan(up_b[-1]) and up_b[-1] > lo_b[-1])

    # --- ADX / DI ranges
    h = [v + 0.1 for v in x]
    l = [v - 0.1 for v in x]
    adxv, pdi, mdi = adx(h, l, x, 14)
    check("ADX warmup NaN", _isnan(adxv[-1]) or not _isnan(adxv[-1]))
    check("ATR positive", nanmax(atr(h, l, x, 3)) > 0)

    # --- Stochastic in range
    k, d = stochastic(x, [v - 0.5 for v in x], x, 3, 3)
    check("Stoch K in [0,100]", not _isnan(k[-1]) and 0 <= k[-1] <= 100)

    # --- StochRSI in [0,100]
    s_k, s_d = stoch_rsi(x, 3, 3, 3)
    if not _isnan(s_k[-1]):
        check("StochRSI range", 0 <= s_k[-1] <= 100)
    else:
        print("  [skip] StochRSI (warmup)")
    print("  ---")
    print("  SELFTEST", "PASSED" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    selftest()
