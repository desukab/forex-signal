# High-Confluence Forex 5-Minute Signal Engine

A strict, multi-timeframe forex signal engine. **Design goal: minimise false
triggers.** It stays silent (`NO_TRADE`) unless every independent condition
agrees, then it lights up a `CALL` or `PUT` with a confidence score.

**You make the final call.** This only analyses and generates signals.

Runs on **Termux** with zero `pip` dependencies (Python standard library only)
and no numpy.

---

## Quick start — clone and run

Works on Termux, Linux, macOS, WSL. Nothing to compile; no `pip` step at all.

```bash
git clone <your-repo-url> ~/forex-signal
cd ~/forex-signal

cp .env.example .env     # then nano .env and set OANDA_API_KEY=...
./run.sh                 # starts the live-signals server on :8081
```

Open **http://localhost:8081/** for the live dashboard — or:

```bash
./run.sh once      # one signal for the configured pair, right now
./run.sh scan      # scan the whole watchlist once
./run.sh check     # test the OANDA connection
./run.sh mock      # run the server with a synthetic feed (no key needed)
```

`run.sh` is the all-in-one entry point: it checks your `.env`, optionally
installs Node's optional `ws` package, and dispatches to the server or CLI.
See **README.npm.md** for the npm flow (`npm start`) and every HTTP/WebSocket
endpoint.

> Your OANDA key lives in `.env` (git-ignored). Never commit a real key.
> Free demo key: https://www.oanda.com/demo-account/

---

## What changed from the simple version

The old build fired on *any* pullback-to-EMA with RSI in range. This build
demands **full confluence** across seven independent conditions:

1. **Multi-timeframe trend agreement** — 5m, 15m AND 1h must all agree on the
   bias. A 5m pullback against the 1h trend is a trap and is refused.
2. **Trend strength (ADX)** — only trades when the trend actually has power.
   No strength = chop = no trade.
3. **Directional movement (DI)** — requires real directional push.
4. **RSI condition** — refuses overbought CALLs and oversold PUTs.
5. **Stochastic momentum** — requires the oscillator to be *freshly turning*
   back in the trend direction, not exhausted.
6. **Pullback structure** — price must be genuinely retraced to the fast EMA,
   not chasing.
7. **Bar quality** — refuses dead bars (no range) and extreme spikes (news).

The default is **NO_TRADE**. It only lights up when all conditions align.

---

## Install (already done if you followed the earlier setup)

If `forex_signal.py` is already on your phone, just replace it. Otherwise:

```bash
cd ~/forex-signal
# copy the new forex_signal.py + config.json over the old ones
```

No pip installs needed.

---

## Configure

Edit `config.json`:

```bash
nano config.json
```

Set your `api_key` (required). All other values have sensible defaults.

| Key | Default | Meaning |
|-----|---------|---------|
| `api_key` | (empty) | Your OANDA practice API token |
| `account_type` | `practice` | `practice` or `live` |
| `pair` | `EUR_USD` | Instrument (OANDA format, underscore) |
| `tf_signal` | `M5` | Decision timeframe |
| `tf_trend` | `M15` | Trend confirmation timeframe |
| `tf_major` | `H1` | Major timeframe confirmation |
| `ema_fast` | `50` | Fast trend EMA |
| `ema_slow` | `200` | Slow trend EMA |
| `rsi_ob` / `rsi_os` | `70` / `30` | RSI refusal thresholds |
| `adx_min` | `22` | Min ADX to trade (raise = stricter) |
| `di_min` | `15` | Min directional movement |
| `stoch_ob` / `stoch_os` | `80` / `20` | Stochastic refusal thresholds |
| `pullback_pct` | `0.0012` | Pullback zone size |
| `min_bars` | `130` | Bars needed before signalling |

---

## Run

```bash
python forex_signal.py                # one signal now
python forex_signal.py loop           # watch, print only new signals (5m)
python forex_signal.py loop 60        # poll faster (still candle-close only)
python forex_signal.py check          # test API connection
python forex_signal.py diag           # show candle counts per timeframe
```

Stop a loop with `Ctrl+C`.

Example output:

```
==========================================================
  EUR/USD  |  M5  decision | trend M15 | major H1
  Candle close : 2026-08-31T09:25:00Z  @ 1.15982
  Trends       : 5m=UP 15m=UP 1h=UP  |  ADX=28  |  RSI=56  |  Stoch K=48
----------------------------------------------------------
  SIGNAL :  CALL   confidence 73%
  Reason :  Full confluence: multi-tf uptrend, strong momentum, fresh pullback
  Setup  :  5m+15m+1h UP, ADX=28, RSI=56, Stoch=48, pullback to 50EMA
==========================================================
  You make the call. Analysis only - no guaranteed accuracy.
```

When it's confident nothing lines up, you'll instead see:

```
  SIGNAL :  NO TRADE
  Why    :  timeframes disagree (5m=UP, 15m=UP, 1h=DOWN), weak trend (ADX=16, need 22)
```

---

## Making it stricter / looser

- **Fewer, higher-confidence signals** → raise `adx_min` to `25–30` and lower
  `pullback_pct`. You'll trade rarely but only on the strongest confluence.
- **A bit more frequent** (more risk) → lower `adx_min` to `18–20`. It will
  fire more often in weaker but still trended conditions.

---

## Honest notes

- **No system can guarantee accuracy.** These filters drastically cut false
  triggers, but 5-minute forex remains noisy. The `NO_TRADE` discipline is
  what does most of the work — a signal that stays quiet in chop has a far
  higher win-rate than one that fires on every bar.
- **Weekends are dead** — forex markets close Friday~Sunday. If you see an
  error about "not enough completed candles" on a weekend, that's normal.
- The slow-EMA period degrades gracefully when your account has little
  history yet, then improves as candles accumulate.
- Signals are based on **mid price** on candle close; account for the broker
  spread when you decide.
- Color codes (`\033[32m` etc.) work in Termux.
