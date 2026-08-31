#!/usr/bin/env python3
"""
High-confluence 5-minute forex signal engine
=============================================
Design goal: MINIMISE false triggers and only signal when multiple
independent conditions agree. Fewer, cleaner signals; the default is
"no trade".

For every closed 5m candle it prints:
    CALL      -> strong uptrend (5m+15m+1h aligned), genuine pullback to the
                 fast EMA, RSI + stochastic turning back up, trend has real
                 strength (ADX)
    PUT       -> mirror image for a downtrend
    NO_TRADE  -> anything less than full confluence (the default)

Data source : OANDA v20 REST API. Runs standalone or inside Fincept.
Run on Termux:  python forex_signal.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config():
    defaults = {
        # --- connection ---
        "api_key": "",
        "account_type": "practice",
        "pair": "EUR_USD",
        # --- data ---
        "tf_signal": "M5",        # decision timeframe
        "tf_trend": "M15",        # trend confirmation
        "tf_major": "H1",         # major timeframe confirmation
        # --- indicators ---
        "ema_fast": 50,
        "ema_slow": 200,
        "rsi_period": 14,
        "rsi_ob": 70,             # refuse CALL when RSI above this
        "rsi_os": 30,             # refuse PUT when RSI below this
        "adx_period": 14,
        "adx_min": 22,            # trend strength required to trade
        "di_min": 15,             # directional movement required
        "stoch_k": 14,
        "stoch_d": 3,
        "stoch_ob": 80,
        "stoch_os": 20,
        # --- pullback structure ---
        "pullback_pct": 0.0012,   # max distance of the pullback low/high from fast EMA
        # --- filters ---
        "min_range_pct": 0.00020, # require at least this 5m bar range (avoid dead bars)
        "max_range_pct": 0.0040,  # refuse extremely volatile bars (news spikes)
        "min_bars": 130,          # bars needed before signalling
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            user = json.load(f)
        defaults.update({k: v for k, v in user.items() if v != ""})
    return defaults


CFG = load_config()

API_HOST = "api-fxpractice.oanda.com" if CFG["account_type"] == "practice" \
    else "api-fxtrade.oanda.com"
BASE_URL = f"https://{API_HOST}/v3"


# ---------------------------------------------------------------------------
# OANDA data layer
# ---------------------------------------------------------------------------

def oanda_headers():
    if not CFG["api_key"]:
        sys.exit(
            "\n[!] No OANDA API key configured. Edit config.json api_key.\n"
            "    Free demo key: https://www.oanda.com/demo-account/\n")
    return {
        "Authorization": f"Bearer {CFG['api_key']}",
        "Content-Type": "application/json",
    }


def _get(url):
    req = urllib.request.Request(url, headers=oanda_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"\n[!] OANDA HTTP {e.code}: {e.read().decode()[:400]}\n"
                 f"    Check api_key / account_type / pair.")
    except urllib.error.URLError as e:
        sys.exit(f"\n[!] Network error: {e}")


def instrument_url():
    return f"{BASE_URL}/instruments/{CFG['pair']}"


def fetch_candles(granularity, count):
    """Return list of closed candles [{t,o,h,l,c}] oldest->newest."""
    url = (f"{instrument_url()}/candles?granularity={granularity}"
           f"&count={count}&price=M&alignmentTimezone=UTC")
    data = _get(url)
    out = []
    for c in data.get("candles", []):
        if not c.get("complete"):
            continue
        mid = c["mid"]
        out.append({"t": c["time"], "o": float(mid["o"]),
                    "h": float(mid["h"]), "l": float(mid["l"]),
                    "c": float(mid["c"])})
    return out


def fetch_all():
    """Pull all three timeframes aligned to the decision candle."""
    # Ask enough major-tf bars so the decision candle overlaps.
    sig = fetch_candles(CFG["tf_signal"], CFG["min_bars"])
    trend = fetch_candles(CFG["tf_trend"], CFG["min_bars"])
    major = fetch_candles(CFG["tf_major"], 60)
    if len(sig) < CFG["min_bars"]:
        sys.exit(f"\n[!] Only {len(sig)} completed {CFG['tf_signal']} candles. "
                 f"Market may be closed (weekend) or account too new. "
                 f"Amount needed: {CFG['min_bars']}.")
    return sig, trend, major


# ---------------------------------------------------------------------------
# Indicators (self-contained)
# ---------------------------------------------------------------------------

def ema(values, period):
    k = 2.0 / (period + 1.0)
    out, e = [], None
    for v in values:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, period):
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    g, l = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g.append(max(ch, 0.0))
        l.append(max(-ch, 0.0))
    ag = sum(g[:period]) / period
    al_ = sum(l[:period]) / period
    out[period] = 100.0 if al_ == 0 else 100.0 - 100.0 / (1.0 + ag / al_)
    for i in range(period, len(closes) - 1):
        ag = (ag * (period - 1) + g[i]) / period
        al_ = (al_ * (period - 1) + l[i]) / period
        out[i + 1] = 100.0 if al_ == 0 else 100.0 - 100.0 / (1.0 + ag / al_)
    return out


def true_range(closes, highs, lows):
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    return tr


def adx(closes, highs, lows, period=14):
    """Return (adx, +DI, -DI) at the last index, or None if insufficient."""
    n = len(closes)
    if n < period * 2 + 2:
        return None, None, None
    tr = true_range(closes, highs, lows)
    up = [0.0] * n
    dn = [0.0] * n
    for i in range(1, n):
        up[i] = max(0.0, highs[i] - highs[i - 1])
        dn[i] = max(0.0, lows[i - 1] - lows[i])
    atr = [0.0] * n
    pdi = [0.0] * n
    mdi = [0.0] * n
    dx = [0.0] * n
    i0 = period
    atr[i0] = sum(tr[1:i0 + 1]) / period
    pdi[i0] = 100.0 * sum(up[1:i0 + 1]) / atr[i0] if atr[i0] else 0.0
    mdi[i0] = 100.0 * sum(dn[1:i0 + 1]) / atr[i0] if atr[i0] else 0.0
    dx[i0] = 100.0 * abs(pdi[i0] - mdi[i0]) / (pdi[i0] + mdi[i0]) if (pdi[i0] + mdi[i0]) else 0.0
    for i in range(i0 + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        pdi[i] = (pdi[i - 1] * (period - 1) + 100.0 * up[i] / atr[i]) / period
        mdi[i] = (mdi[i - 1] * (period - 1) + 100.0 * dn[i] / atr[i]) / period
        dx[i] = 100.0 * abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i]) if (pdi[i] + mdi[i]) else 0.0
    if n < i0 + period + 2:
        return None, pdi[i], mdi[i]
    adxv = [0.0] * n
    adxv[i0 + period] = sum(dx[i0 + 1:i0 + period + 1]) / period
    for i in range(i0 + period + 1, n):
        adxv[i] = (adxv[i - 1] * (period - 1) + dx[i]) / period
    return adxv[-1], pdi[-1], mdi[-1]


def stochastic(highs, lows, closes, k_period=14, d_period=3):
    """Return (k, d) at last index (percent-K, smoothed percent-D)."""
    n = len(closes)
    if n < k_period + d_period:
        return None, None
    kvals = []
    for i in range(k_period - 1, n):
        hh = max(highs[i - k_period + 1:i + 1])
        ll = min(lows[i - k_period + 1:i + 1])
        rng = hh - ll
        kvals.append(50.0 if rng == 0 else 100.0 * (closes[i] - ll) / rng)
    k = kvals[-1]
    if len(kvals) >= d_period:
        d = sum(kvals[-d_period:]) / d_period
    else:
        d = k
    return k, d


def direction_label(trend):
    return "UP" if trend > 0 else ("DOWN" if trend < 0 else "FLAT")


def tf_trend_direction(bars, ema_fast, ema_slow):
    """Return +1 up, -1 down, 0 flat from fast vs slow EMA of a tf's closes.
    Slow period degrades gracefully but is always kept LARGER than fast
    (comparing a longer EMA vs a shorter one, so faster hugs price more)."""
    closes = [b["c"] for b in bars]
    if len(closes) < ema_fast + 3:
        return 0
    slow = min(ema_slow, len(closes) // 2)
    slow = max(slow, ema_fast + 2, 20)   # ensure slow > fast
    ef = ema(closes, ema_fast)[-1]
    es = ema(closes, slow)[-1]
    if ef > es:
        return 1
    if ef < es:
        return -1
    return 0


# ---------------------------------------------------------------------------
# Signal engine
# ---------------------------------------------------------------------------

def generate_signal(sig, trend, major):
    closes = [b["c"] for b in sig]
    highs = [b["h"] for b in sig]
    lows = [b["l"] for b in sig]

    i = len(sig) - 1
    price = closes[i]
    ema_f = ema(closes, CFG["ema_fast"])
    ema_s_full = ema(closes, CFG["ema_slow"]) if len(closes) >= CFG["ema_slow"] else \
        ema(closes, max(20, len(closes) // 2))
    ef = ema_f[i]
    es = ema_s_full[i]

    # --- 1. MTF trend alignment ---
    bias5 = 1 if ef > es else (-1 if ef < es else 0)
    bias15 = tf_trend_direction(trend, CFG["ema_fast"], 60)
    biasH1 = tf_trend_direction(major, CFG["ema_fast"], 100)
    aligned = (bias5 != 0 and bias15 == bias5 and biasH1 == bias5)

    # --- 2. ADX strength ---
    a, pdi, mdi = adx(closes, highs, lows, CFG["adx_period"])
    strong = a is not None and a >= CFG["adx_min"] and \
        max(pdi, mdi) >= CFG["di_min"]
    up_strength = pdi > mdi

    # --- 3. RSI ---
    r = rsi(closes, CFG["rsi_period"])[i]

    # --- 4. Stochastic momentum ---
    k, d = stochastic(highs, lows, closes, CFG["stoch_k"], CFG["stoch_d"])

    # --- 5. Pullback structure ---
    tol = CFG["pullback_pct"] * price
    # For a CALL: price pulled back so the bar's low touched/dipped to the EMA
    # but is still inside the trend (close near/just below fast EMA, above slow).
    pullup = (lows[i] <= ef + tol) and (closes[i] <= ef + 2 * tol)
    pulldn = (highs[i] >= ef - tol) and (closes[i] >= ef - 2 * tol)

    # --- 6. Bar quality (range guard) ---
    bar_range = highs[i] - lows[i]
    too_dead = bar_range < CFG["min_range_pct"] * price
    too_spike = bar_range > CFG["max_range_pct"] * price

    reason_lines = []
    if not aligned:
        reason_lines.append(f"timeframes disagree (5m={direction_label(bias5)}, "
                            f"15m={direction_label(bias15)}, 1h={direction_label(biasH1)})")
    if a is None or a < CFG["adx_min"]:
        reason_lines.append(f"weak trend (ADX={a if a is not None else 'n/a'}, need {CFG['adx_min']})")
    if max(pdi or 0, mdi or 0) < CFG["di_min"]:
        reason_lines.append(f"low directional move (DI {pdi:.0f}/{mdi:.0f})")
    if too_dead:
        reason_lines.append("dead bar (no range)")
    if too_spike:
        reason_lines.append("spike bar (likely news)")

    def _stoch_ok_up():
        return k is not None and k < CFG["stoch_ob"] and (d or 0) < CFG["stoch_ob"]

    def _stoch_ok_dn():
        return k is not None and k > CFG["stoch_os"] and (d or 0) > CFG["stoch_os"]

    signal, conf, reason, setup, level = "NO_TRADE", 0.0, "no confluence", [], "BAR"
    why = None
    if aligned and strong and not too_dead and not too_spike:
        if bias5 == 1 and up_strength and pullup and r is not None and \
                r < CFG["rsi_ob"] and _stoch_ok_up():
            signal = "CALL"
            why = "uptrend pullback"
            # confidence weighted by how clearly conditions agree
            conf = 0.55
            conf += 0.10 * (1 if a >= CFG["adx_min"] + 4 else 0)
            conf += 0.10 * (1 if bias15 == 1 and biasH1 == 1 else 0)
            conf += 0.10 * (1 if pdi > mdi + 8 else 0)
            conf += 0.05 * (1 if (d or 0) < 45 else 0)
            conf += 0.10 * (1 - min(1.0, (ef - price) / (tol * 4)))  # deeper pullback = better
            setup = ["5m+15m+1h UP", f"ADX={a:.0f}", f"RSI={r:.0f}",
                     f"Stoch={k:.0f}", "pullback to 50EMA"]
            reason = "Full confluence: multi-tf uptrend, strong momentum, fresh pullback"
        elif bias5 == -1 and not up_strength and pulldn and r is not None and \
                r > CFG["rsi_os"] and _stoch_ok_dn():
            signal = "PUT"
            why = "downtrend pullback"
            conf = 0.55
            conf += 0.10 * (1 if a >= CFG["adx_min"] + 4 else 0)
            conf += 0.10 * (1 if bias15 == -1 and biasH1 == -1 else 0)
            conf += 0.10 * (1 if mdi > pdi + 8 else 0)
            conf += 0.05 * (1 if (d or 0) > 55 else 0)
            conf += 0.10 * (1 - min(1.0, (price - ef) / (tol * 4)))
            setup = ["5m+15m+1h DOWN", f"ADX={a:.0f}", f"RSI={r:.0f}",
                     f"Stoch={k:.0f}", "rally to 50EMA"]
            reason = "Full confluence: multi-tf downtrend, strong momentum, fresh rally"
        else:
            if r is not None and r >= CFG["rsi_ob"] and bias5 == 1:
                why = "RSI overbought - refuse CALL"
            elif r is not None and r <= CFG["rsi_os"] and bias5 == -1:
                why = "RSI oversold - refuse PUT"
            elif not (pullup if bias5 == 1 else pulldn):
                why = "not at a fresh pullback zone"
            elif not (_stoch_ok_up() if bias5 == 1 else _stoch_ok_dn()):
                why = "stochastic exhausted (no momentum turn)"
            else:
                why = "direction mismatch"
            reason_lines.append(why)

    if not reason_lines and signal != "NO_TRADE":
        pass
    if not reason_lines:
        reason_lines = ["no confluence"]
    reason_final = ", ".join(reason_lines) if signal == "NO_TRADE" else reason

    return {
        "signal": signal,
        "confidence": round(min(conf, 0.98), 2),
        "reason": reason_final,
        "price": price,
        "rsi": r,
        "stoch": k,
        "adx": a,
        "di": (pdi, mdi),
        "ema_fast": ef,
        "ema_slow": es,
        "trend5": direction_label(bias5),
        "trend15": direction_label(bias15),
        "trend_h1": direction_label(biasH1),
        "time": sig[i]["t"],
        "setup": setup,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def fmt(p):
    return f"{p:.5f}" if p < 10 else f"{p:.4f}"


def render(s):
    pair = CFG["pair"].replace("_", "/")
    print("=" * 58)
    print(f"  {pair}  |  {CFG['tf_signal']}  decision | trend {CFG['tf_trend']} | major {CFG['tf_major']}")
    print(f"  Candle close : {s['time']}  @ {fmt(s['price'])}")
    print(f"  Trends       : 5m={s['trend5']} 15m={s['trend15']} 1h={s['trend_h1']}"
          f"  |  ADX={s['adx'] if s['adx'] is not None else 'n/a':.0f}"
          f"  |  RSI={s['rsi']:.0f}  |  Stoch K={s['stoch']:.0f}")
    print("-" * 58)
    if s["signal"] == "NO_TRADE":
        print(f"  SIGNAL :  NO TRADE")
        print(f"  Why    :  {s['reason']}")
    else:
        col = "\033[32m" if s["signal"] == "CALL" else "\033[31m"
        print(f"  SIGNAL :  {col}{s['signal']}\033[0m   confidence {s['confidence']:.0%}")
        print(f"  Reason :  {s['reason']}")
        print(f"  Setup  :  {', '.join(s['setup'])}")
    print("=" * 58)
    print("  You make the call. Analysis only - no guaranteed accuracy.")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode in ("once", "signal"):
        sig, trend, major = fetch_all()
        render(generate_signal(sig, trend, major))
        return
    if mode == "loop":
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 300
        print(f"[*] Watching every {interval}s. Prints only new signals. Ctrl-C to stop.")
        seen = set()
        try:
            while True:
                sig, trend, major = fetch_all()
                s = generate_signal(sig, trend, major)
                if s["signal"] != "NO_TRADE" and s["time"] not in seen:
                    print(f"\n[{time.strftime('%H:%M:%S')}] NEW SIGNAL: "
                          f"{s['signal']} conf={s['confidence']:.0%} "
                          f"ADX={s['adx']:.0f} RSI={s['rsi']:.0f} -> {s['reason']}")
                    seen.add(s["time"])
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[*] Stopped.")
        return
    if mode == "check":
        c = fetch_candles(CFG["tf_signal"], 3)
        print(f"[OK] OANDA works. Got {len(c)} closed {CFG['tf_signal']} candles.")
        return
    if mode == "diag":
        for g in (CFG["tf_signal"], CFG["tf_trend"], CFG["tf_major"]):
            c = fetch_candles(g, 5)
            first = c[0]["t"] if c else "none"
            last = c[-1]["t"] if c else "none"
            print(f"{g:4s}: {len(c)} closed candles   first={first}  last={last}")
        return
    print(__doc__)
    print("Usage:\n  python forex_signal.py             one signal now\n"
          "  python forex_signal.py loop [secs]    watch continuously\n"
          "  python forex_signal.py check          test connection\n"
          "  python forex_signal.py diag           show candle counts per tf")


if __name__ == "__main__":
    main()
