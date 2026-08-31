#!/usr/bin/env python3
"""v2 Professional Binary-Options Signal Engine — CLI.

Runs purely on Termux. OANDA is used only as a market-data feed to decide
CALL/PUT direction; you make the final call on every trade.

Commands
--------
  signal.py once              one signal for config's pair, now
  signal.py check             test OANDA connectivity
  signal.py diag              candle counts per configured timeframe
  signal.py selftest          verify the indicator library on fixtures
  signal.py scan [model]      evaluate every pair in watchlist, print signals
  signal.py loop [secs]       watch a pair, print/notify only NEW closed-bar signals
  signal.py stream [p] [--mock] [--every S]   machine-readable JSON-lines feed (npm server)
  signal.py backtest <sym>    replay history and report metrics at a chosen expiry
  signal.py optimize <sym>    sweep expiries 1/2/3/5m per pair, pick the best
  signal.py optimize-all      run the optimizer across the whole watchlist

Exit codes: 0 ok, 1 usage/error. Data-dependent commands need the market open
(OANDA history only grows on trading days).
"""
from __future__ import annotations

import sys
import time as _time

from engine.configutil import CFG, get, load_watchlist

G = "\033[32m"
R = "\033[31m"
Y = "\033[33m"
B = "\033[34m"
N = "\033[0m"


def _fmt(p: float) -> str:
    return f"{p:.5f}" if p < 10 else f"{p:.4f}"


def _print_signal(sig):
    sym = sig.symbol.replace("_", "/")
    col = G if sig.side == "CALL" else R
    print("=" * 58)
    print(f"  SIGNAL : {col}{sig.side}{N}  {sym}   model={sig.model}")
    print(f"  Confidence : {sig.confidence:.0%}")
    print(f"  Price      : {_fmt(sig.price)}")
    print(f"  Expiry hint: ~{sig.expiry_hint}m")
    print(f"  Close bar  : {sig.time}")
    print(f"  Setup      : {', '.join(sig.reasons)}")
    if sig.indicators:
        ind = ", ".join(f"{k}={v}" for k, v in sig.indicators.items()
                        if v is not None and k not in ("bias1", "biasC", "biasM"))
        if ind:
            print(f"  Ind        : {ind}")
    print("=" * 58)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_once(pair: str | None = None):
    from engine.engine import analyze_symbol
    sym = pair or get(CFG, "pair", "EUR_USD")
    sigs = analyze_symbol(sym)
    if not sigs:
        print(f"{Y}[*]{N} {sym.replace('_','/')}: NO TRADE (full confluence required).")
        print("    Only trades when every independent condition agrees.")
        return
    for s in sigs:
        _print_signal(s)
    print("  You make the call. Analysis only - no guaranteed accuracy.")


def cmd_check():
    from engine import oanda
    from engine.configutil import get as _g
    sym = _g(CFG, "pair", "EUR_USD")
    rows = oanda.fetch_candles(sym, get(CFG, "tf_decision", "M1"), 3)
    print(f"[OK] OANDA works. Got {len(rows)} closed "
          f"{get(CFG, 'tf_decision', 'M1')} candles for {sym}.")
    if rows:
        p = oanda.pricing(sym)
        if p:
            print(f"     Last mid={p['bid'] if 'bid' in p else ''}  "
                  f"spread≈{p.get('spread', 0):.5f}  @ {p['time']}")


def cmd_diag():
    from engine import oanda
    from engine.configutil import get as _g
    sym = _g(CFG, "pair", "EUR_USD")
    tfs = [_g(CFG, "tf_decision", "M1"), "M5", "M15"]
    for tf in dict.fromkeys(tfs):
        try:
            rows = oanda.fetch_candles(sym, tf, 10)
            first = rows[0]["t"] if rows else "none"
            last = rows[-1]["t"] if rows else "none"
            print(f"  {tf:4s}: {len(rows)} closed candles  first={first}  last={last}")
        except Exception as e:
            print(f"  {tf:4s}: ERROR {e}")


def cmd_selftest():
    from engine.indicators import selftest
    ok = selftest()
    sys.exit(0 if ok else 1)


def cmd_scan(model: str | None = None):
    from engine.engine import analyze_symbol
    watchlist = load_watchlist()
    if not watchlist:
        print("[!] watchlist.json has no pairs.")
        return
    print(f"{B}[*]{N} Scanning {len(watchlist)} pairs "
          f"(model={model or get(CFG,'model','both')})...\n")
    any_sig = False
    for entry in watchlist:
        sym = entry["symbol"]
        # crude model filter: if user asked a single model, only run that
        if model and model.upper() == "REVERSAL":
            from engine.reversal import prepare_reversal, evaluate_reversal_at
            from engine import oanda
            rows = oanda.fetch_candles(sym, get(CFG, "tf_decision", "M1"), 500)
            if len(rows) < 60:
                print(f"  {sym.replace('_','/')}: {Y}skip (no data){N}")
                continue
            prep = prepare_reversal(oanda.to_arrays(rows), CFG)
            sig = evaluate_reversal_at(prep, -1, sym, rows[-1]["t"],
                                       entry.get("expiry_hint", 5))
            if sig:
                any_sig = True
                _print_signal(sig)
        elif model and model.upper() == "MOMENTUM":
            from engine.engine import _upper_series
            from engine.momentum import prepare_momentum, evaluate_momentum_at
            from engine import oanda
            rows = oanda.fetch_candles(sym, get(CFG, "tf_decision", "M1"), 500)
            if len(rows) < 60:
                print(f"  {sym.replace('_','/')}: {Y}skip (no data){N}")
                continue
            arr = oanda.to_arrays(rows)
            confirm = _upper_series(sym, "M5")
            major = _upper_series(sym, "M15")
            prep = prepare_momentum(arr, confirm, major, CFG)
            sig = evaluate_momentum_at(prep, -1, sym, rows[-1]["t"],
                                       entry.get("expiry_hint", 5))
            if sig:
                any_sig = True
                _print_signal(sig)
        else:
            sigs = analyze_symbol(sym)
            if sigs:
                any_sig = True
                for s in sigs:
                    _print_signal(s)
    if not any_sig:
        print(f"{Y}[*]{N} No actionable signals across the watchlist right now.")


def cmd_stream(pair: str | None, every: int, mock: bool):
    """Machine-readable live mode: one JSON object per line on stdout.

    This is the seam the npm server consumes. Every line is a standalone JSON
    object so the consumer can parse line-by-line without shared state:

      {"type":"ready","pair":...,"decision_tf":...,"version":...}
      {"type":"signal","symbol":...,"side":"CALL","model":"REVERSAL",...}
      {"type":"status","note":"no API key configured",...}

    ``--mock`` emits synthetic signals so the Node layer is testable without
    numpy / a live OANDA feed (handy for dev). The real path is the default.
    """
    import json as _json

    from engine.configutil import get as _g

    sym = (pair or _g(CFG, "pair", "EUR_USD")).upper()
    tf_dec = _g(CFG, "tf_decision", "M1")
    version = "2.0"

    def emit(obj: dict):
        sys.stdout.write(_json.dumps(obj) + "\n")
        sys.stdout.flush()

    emit({"type": "ready", "pair": sym, "decision_tf": tf_dec,
          "model": _g(CFG, "model", "both"), "version": version, "mock": bool(mock)})

    if not _g(CFG, "api_key"):
        emit({"type": "status", "ok": False,
              "note": "no OANDA API key configured (set OANDA_API_KEY in .env)"})

    if mock:
        _stream_mock(sym, every, emit)
        return

    from engine.engine import analyze_symbol

    seen = set()
    try:
        while True:
            try:
                sigs = analyze_symbol(sym)
                for s in sigs:
                    key = (s.model, s.side, s.time)
                    if key in seen:
                        continue
                    seen.add(key)
                    emit({
                        "type": "signal",
                        "symbol": s.symbol, "side": s.side, "model": s.model,
                        "confidence": round(s.confidence, 4),
                        "price": s.price, "expiry_hint": s.expiry_hint,
                        "time": s.time, "reasons": s.reasons,
                        "indicators": s.indicators,
                    })
                if not sigs:
                    emit({"type": "tick", "note": "NO_TRADE", "at": _time.time()})
            except Exception as e:  # keep the stream alive across transient errors
                emit({"type": "error", "note": str(e)})
            _time.sleep(every)
    except KeyboardInterrupt:
        sys.stderr.write("\n[*] stream stopped.\n")


def _stream_mock(sym: str, every: int, emit):
    """Synthetic signal stream for testing the npm layer without numpy/OANDA."""
    import itertools
    models = itertools.cycle(["REVERSAL", "MOMENTUM"])
    sides = itertools.cycle(["CALL", "PUT"])
    t0 = _time.time()
    n = 0
    try:
        while True:
            _time.sleep(every)
            n += 1
            emit({
                "type": "signal",
                "symbol": sym, "side": next(sides), "model": next(models),
                "confidence": round(0.5 + 0.2 * (n % 3), 4),
                "price": round(1.08 + 0.0001 * n, 5),
                "expiry_hint": 5, "time": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
                "reasons": ["MOCK signal for npm server test"],
                "indicators": {"adx": 26.0, "rsi": 58.0, "elapsed_s": round(_time.time() - t0, 1)},
            })
    except KeyboardInterrupt:
        sys.stderr.write("\n[*] mock stream stopped.\n")


def cmd_loop(pair: str | None, interval: int):
    from engine.engine import analyze_symbol
    from notifier.notify import push_signal
    sym = pair or get(CFG, "pair", "EUR_USD")
    print(f"[*] Watching {sym.replace('_','/')} every {interval}s. "
          f"Prints only NEW closed-bar signals. Ctrl-C to stop.")
    seen = set()
    try:
        while True:
            try:
                sigs = analyze_symbol(sym)
                for s in sigs:
                    key = (s.model, s.side, s.time)
                    if key not in seen:
                        seen.add(key)
                        _print_signal(s)
                        pushed = push_signal(s)
                        for ch, ok in pushed.items():
                            if ok:
                                print(f"  [notify] {ch}: sent")
            except Exception as e:
                print(f"  [!] {e}", file=sys.stderr)
            _time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[*] Stopped.")


def cmd_backtest(pair: str, expiry: int | None):
    from backtest.runner import replay_symbol
    from backtest.metrics import Metrics
    from engine.configutil import get as _g
    bt = _g(CFG, "backtest", {})
    payout = float(_g(bt, "payout", 0.8))
    stake = float(_g(bt, "stake", 1.0))
    if expiry is None:
        expiry = 3  # default expiry (bars) when not specified
    trades = replay_symbol(pair, expiry, model="both")
    m = Metrics(symbol=pair, expiry_bars=expiry, trades=trades)
    print(f"=== Backtest: {pair.replace('_','/')}  expiry={expiry} bar(s) ===")
    print(m.report(payout, stake))


def cmd_optimize(pair: str):
    from backtest.optimizer import optimize_report
    print(optimize_report(pair, model="both"))


def cmd_optimize_all():
    from backtest.optimizer import optimize_report
    from backtest.optimizer import best_expiry, optimize_symbol
    from backtest.metrics import Metrics
    from engine.configutil import get as _g
    bt = _g(CFG, "backtest", {})
    payout = float(_g(bt, "payout", 0.8))
    watchlist = load_watchlist()
    if not watchlist:
        print("[!] watchlist.json has no pairs.")
        return
    print(f"{B}[*]{N} Optimizing {len(watchlist)} pairs (this fetches history, "
          f"may take a while)...\n")
    summary = []
    for entry in watchlist:
        sym = entry["symbol"]
        try:
            lines = optimize_report(sym, model="both")
            # print full report (could be long); keep to the summary below
            print(lines)
            print()
            best = best_expiry(optimize_symbol(sym))
            if best:
                n, m = best
                summary.append((sym, n, m.win_rate, m.n))
            else:
                summary.append((sym, None, 0.0, 0))
        except Exception as e:
            print(f"  {sym}: ERROR {e}")
    print("\n=== Best per pair (target >60%) ===")
    for sym, n, wr, cnt in summary:
        ok = "YES" if wr > 0.60 and cnt >= int(_g(bt, "min_signals", 20)) else "no"
        print(f"  {sym.replace('_','/'):<12} expiry={str(n):<4} wr={wr:<8.1%} "
              f"n={cnt:<4} meets60={ok}")


USAGE = __doc__


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd = argv[0]

    try:
        if cmd == "once":
            cmd_once(argv[1] if len(argv) > 1 else None)
        elif cmd in ("check", "test"):
            cmd_check()
        elif cmd == "diag":
            cmd_diag()
        elif cmd == "selftest":
            cmd_selftest()
        elif cmd == "scan":
            cmd_scan(argv[1] if len(argv) > 1 else None)
        elif cmd == "loop":
            interval = int(argv[2]) if len(argv) > 2 else 60
            pair = argv[1] if len(argv) > 1 else None
            cmd_loop(pair, interval)
        elif cmd == "stream":
            pair = None
            every = 60
            mock = False
            args = argv[1:]
            i = 0
            while i < len(args):
                a = args[i]
                if a == "--mock":
                    mock = True
                elif a == "--every":
                    every = int(args[i + 1]); i += 1
                elif a.startswith("--every="):
                    every = int(a.split("=", 1)[1])
                elif a.startswith("-"):
                    print(f"[!] Unknown stream flag: {a}")
                    return 1
                else:
                    pair = a.upper()
                i += 1
            cmd_stream(pair, every, mock)
        elif cmd == "backtest":
            if len(argv) < 2:
                print("[!] usage: signal.py backtest <PAIR> [expiry_bars]")
                return 1
            expiry = int(argv[2]) if len(argv) > 2 else None
            cmd_backtest(argv[1].upper(), expiry)
        elif cmd == "optimize":
            if len(argv) < 2:
                print("[!] usage: signal.py optimize <PAIR>")
                return 1
            cmd_optimize(argv[1].upper())
        elif cmd == "optimize-all":
            cmd_optimize_all()
        else:
            print(f"[!] Unknown command: {cmd}")
            print(USAGE)
            return 1
    except Exception as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
