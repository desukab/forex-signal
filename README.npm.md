# Live signals via npm

Run the signal engine as a local server, `npm start`, and stream **CALL / PUT**
signals to a browser dashboard, a WebSocket client, or any HTTP consumer.

```
npm start                  → runs  node server.js   (real engine)
npm run mock               → runs  node server.js --mock  (synthetic demo feed)
```

⚠️ This is **analysis only**. The server never places a trade — you make the
final call.

---

## 1. Install

```bash
cd ~/forex-signal
npm install              # pulls the `ws` package for WebSocket
```

> No pip / numpy needed — the engine is 100% Python stdlib. If you're only using
> HTTP + SSE (no WebSocket client), even `npm install` is optional.

## 2. Set your OANDA key

Copy the template and paste your practice key:

```bash
cp .env.example .env
nano .env                  # set  OANDA_API_KEY=...
```

> The key is read from `.env` (or an `OANDA_API_KEY` env var) and overrides the
> blank `api_key` in `config.json`. `engine/configutil.py` and `server.js` both
> read this chain, so the dashboard's "API key configured" chip reflects it.

## 3. Start

```bash
npm start
```

Then open **http://localhost:8081/** — the dashboard. New closed-bar signals
appear at the top as green CALL / red PUT cards, prefetched live over
Server-Sent Events.

---

## Endpoints

| Endpoint            | What it does                                              |
|---------------------|-----------------------------------------------------------|
| `GET /`             | Live dashboard (SSE-fed, no JS build step)                |
| `GET /api/signals`  | Last 50 signals as JSON                                   |
| `GET /api/status`   | `{apiKeyConfigured, engine, engineError, lastSignal, …}`  |
| `GET /sse`          | Server-Sent Events stream of every new signal             |
| `ws://localhost:8081` | WebSocket broadcast of every new signal                |

### WebSocket client (any language)

```js
const WebSocket = require("ws");
const ws = new WebSocket("ws://localhost:8081");
ws.on("message", d => console.log(JSON.parse(d).side));   // "CALL" / "PUT"
```

Signals arrive as one JSON object per line, e.g.:

```json
{"type":"signal","symbol":"EUR_USD","side":"CALL","model":"REVERSAL",
 "confidence":0.73,"price":1.15982,"expiry_hint":2,"time":"...",
 "reasons":["pierce upper band + StochRSI cross down + bearish close"],
 "indicators":{"adx":28,"rsi":56}}
```

---

## How it's wired

`npm start` boots `server.js`, which spawns the Python engine as a subprocess:

```
server.js  ──spawns──►  python3 signal.py stream EUR_USD --every 60
   │                          │  JSON-lines on stdout
   │                          ▼
   │   parses → Signal objects
   └─► dashboard / /api/signals / /api/status / /sse / WebSocket
```

`signal.py stream` is a new machine-readable mode (the human `loop` command is
unchanged). It reuses the exact same `engine.engine.analyze_symbol` call.

### Options

| Flag            | Meaning                                        |
|-----------------|------------------------------------------------|
| `--mock`        | Synthetic signals (dev / testing, no numpy/API)|
| `--pair GBP_USD`| Override the instrument                          |
| `--port 9000`   | Override the port (default `8081`)             |
| `--every 30`    | Poll seconds (default from config / 60)        |

### If the dashboard shows "NO API KEY"

Live signals are gated on a real key. Add it to `.env` and restart. The engine
is stdlib-only, so there's no Python dependency step to worry about.

---

## Verifying it works here (without a market feed)

```bash
npm run mock        # node server.js --mock
curl -s localhost:8081/api/status     # engine:"running", mock:true
```

`--mock` exists precisely so the Node layer is testable before a live feed is
available. On a live market, `npm start` (no flag) uses the real engine.
