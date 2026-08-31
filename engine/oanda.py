"""OANDA v20 REST data layer for the v2 signal engine.

Uses only the Python standard library (urllib) so it installs cleanly on
Termux with nothing beyond numpy. Responsibilities:

  * authenticated GET requests to api-fxpractice / api-fxtrade
  * closed-candle fetching (``complete`` filter == non-repainting guarantee)
  * paginated historical fetch for backtesting
  * multi-timeframe aggregation anchored to the decision candle
  * live pricing / account introspection

Trade requests are deliberately NOT implemented: this product only analyses
and signals; the operator decides whether to take a binary trade elsewhere.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .configutil import CFG, get

API_HOST = (
    "api-fxpractice.oanda.com"
    if CFG.get("account_type", "practice") == "practice"
    else "api-fxtrade.oanda.com"
)
BASE_URL = f"https://{API_HOST}/v3"


class OandaError(RuntimeError):
    """Raised for HTTP / API failures with a helpful message."""


def _check_key():
    if not CFG.get("api_key"):
        raise OandaError(
            "\n[!] No OANDA API key configured.\n"
            "    Set api_key in config.json, or OANDA_API_KEY in .env.\n"
            "    Free demo key: https://www.oanda.com/demo-account/\n"
        )


def _headers() -> dict:
    _check_key()
    return {
        "Authorization": f"Bearer {CFG['api_key']}",
        "Content-Type": "application/json",
        "Accept-Datetime-Format": "RFC3339",
    }


def _get(path: str, params: dict | None = None) -> dict:
    if params:
        path = f"{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise OandaError(
            f"\n[!] OANDA HTTP {e.code}: {e.read().decode()[:400]}\n"
            f"    Check api_key / account_type / symbol."
        ) from e
    except urllib.error.URLError as e:
        raise OandaError(f"\n[!] Network error reaching OANDA: {e}") from e


# ---------------------------------------------------------------------------
# Account / instruments
# ---------------------------------------------------------------------------


def account():
    try:
        return _get("/accounts/{id}".format(id=CFG.get("account_id", "")))
    except OandaError:
        return None  # account_id optional in this build


def instruments() -> list[dict]:
    """List tradeable instruments (for watchlist validation)."""
    data = _get("/instruments")
    return data.get("instruments", [])


def validate_symbol(symbol: str) -> bool:
    """True if the symbol is known / shaped like an OANDA instrument."""
    return bool(symbol) and "_" in symbol and len(symbol.split("_")) == 2


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------


def candle_rows(data: dict) -> list[dict]:
    """Extract closed candles as [{t,o,h,l,c,vol}] oldest -> newest."""
    out = []
    for c in data.get("candles", []):
        if not c.get("complete"):
            continue  # never signal on a forming candle (non-repaint)
        mid = c["mid"]
        out.append(
            {
                "t": c["time"],
                "o": float(mid["o"]),
                "h": float(mid["h"]),
                "l": float(mid["l"]),
                "c": float(mid["c"]),
                "v": int(c.get("volume", 0)),
            }
        )
    out.sort(key=lambda r: r["t"])
    return out


def fetch_candles(symbol: str, granularity: str, count: int) -> list[dict]:
    """Return up to ``count`` most-recent CLOSED candles (oldest->newest)."""
    data = _get(
        f"/instruments/{symbol}/candles",
        {"granularity": granularity, "count": count, "price": "M"},
    )
    rows = candle_rows(data)
    return rows


def fetch_history(
    symbol: str,
    granularity: str,
    days_back: int,
) -> list[dict]:
    """Fetch up to `days_back` of history, paginating bounded batches.

    OANDA caps each /candles response (effectively ~5000 candles even when a
    wide ``from``/``to`` range is given). A 30-day M5 span is ~8640 candles, so
    a single request would be rejected. We therefore break the window into
    ~1-day sub-windows and request each one separately, then merge + dedupe.
    """
    window = timedelta(days=days_back)
    now = datetime.now(timezone.utc)

    # Each request window must stay under OANDA's ~5000-candle response cap.
    # Break the total window into 1-day chunks: even the densest granularity
    # (M1 = 1440 candles/day) stays well under the cap per request.
    chunk = timedelta(days=1)

    all_rows: dict[str, dict] = {}
    end = now
    while end > now - window:
        start = max(end - chunk, now - window)
        data = _get(
            f"/instruments/{symbol}/candles",
            {
                "granularity": granularity,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "price": "M",
            },
        )
        rows = candle_rows(data)
        for r in rows:
            all_rows[r["t"]] = r
        if not rows:
            break
        end = start
    return sorted(all_rows.values(), key=lambda r: r["t"])


def anchor_buckets(rows: list[dict], confirm_step: int) -> dict:
    """Aggregate a 1-step series into confirm-step buckets aligned to closes.

    Given rows at the decision granularity (e.g. M1) and a confirm step in
    *decision bars* (e.g. 5 for M5), returns {bucket_key: {o,h,l,c,vol,ts}}
    for every closed bucket. Buckets are formed by grouping consecutive
    decision candles; the bucket is 'closed' only when its final decision
    candle is complete (non-repainting across timeframes).
    """
    buckets: list[dict] = []
    for bar_i, r in enumerate(rows):
        key = bar_i // confirm_step
        while len(buckets) <= key:
            buckets.append(None)
        b = buckets[key]
        if b is None:
            buckets[key] = {
                "t": r["t"], "o": r["o"], "h": r["h"], "l": r["l"],
                "c": r["c"], "v": r["v"],
            }
        else:
            b["h"] = max(b["h"], r["h"])
            b["l"] = min(b["l"], r["l"])
            b["c"] = r["c"]
            b["v"] += r["v"]
    # A confirm-timeframe bucket is only 'closed' when every decision bar in
    # it is complete. Since rows are all closed, a bucket is complete iff it
    # is not the trailing partial bucket.
    n_full = len(rows) // confirm_step
    return {k: buckets[k] for k in range(n_full) if buckets[k] is not None}


def pricing(symbol: str) -> dict:
    """Current bid/ask/mid for a symbol (for spread context only)."""
    data = _get(f"/instruments/{symbol}/candles", {"count": 1, "price": "BA"})
    candles = data.get("candles", [])
    if not candles:
        return {}
    c = candles[0]
    return {
        "bid": float(c["bid"]["c"]),
        "ask": float(c["ask"]["c"]),
        "spread": float(c["ask"]["c"]) - float(c["bid"]["c"]),
        "time": c["time"],
    }


# ---------------------------------------------------------------------------
# Convenience: raw arrays for indicator math (plain Python lists, zero numpy)
# ---------------------------------------------------------------------------


def to_arrays(rows: list[dict] | dict) -> tuple:
    """Convert candle rows to (open, high, low, close, volume) Python lists.

    Accepts a list of rows or a bucket dict.
    """
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not rows:
        return ([], [], [], [], [])
    o = [r["o"] for r in rows]
    h = [r["h"] for r in rows]
    l = [r["l"] for r in rows]
    c = [r["c"] for r in rows]
    v = [r["v"] for r in rows]
    return o, h, l, c, v
