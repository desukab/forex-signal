#!/usr/bin/env node
/**
 * Live-signals server for the OANDA signal engine.
 *
 * Spawns the Python engine (`python3 signal.py stream`) and streams CALL/PUT
 * signals to four consumers:
 *   • HTTP   GET /             — live dashboard (SSE-fed, no JS deps)
 *   • HTTP   GET /api/signals  — last N signals as JSON
 *   • HTTP   GET /api/status   — engine + API-key status
 *   • HTTP   GET /sse          — Server-Sent Events live feed
 *   • WebSocket (ws) on :PORT  — broadcast of every new signal
 *
 * Usage:
 *   npm start            real engine (needs numpy + OANDA_API_KEY)
 *   node server.js --mock     synthetic signals (dev / sandbox)
 *   node server.js --port 9000 --pair GBP_USD
 *
 * Signals are analysis-only. This server never places trades.
 */
"use strict";

const http = require("http");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

// ---- config ---------------------------------------------------------------
const ROOT = __dirname;
const CONFIG_PATH = path.join(ROOT, "config.json");
const ENV_PATH = path.join(ROOT, ".env");

// Minimal .env reader (KEY=VALUE, # comments) — enough for OANDA_API_KEY.
function loadDotEnv() {
  if (!fs.existsSync(ENV_PATH)) return {};
  const out = {};
  for (const line of fs.readFileSync(ENV_PATH, "utf8").split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#") || !t.includes("=")) continue;
    const [k, ...rest] = t.split("=");
    out[k.trim()] = rest.join("=").trim().replace(/^["']|["']$/g, "");
  }
  return out;
}

function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
  } catch {
    return {};
  }
}

const dotenv = loadDotEnv();
const cfg = readConfig();

function arg(v, name, def) {
  const i = process.argv.indexOf("--" + name);
  return i !== -1 && process.argv[i + 1] !== undefined ? process.argv[i + 1] : def;
}
const MOCK = process.argv.includes("--mock");
const PORT = parseInt(arg(null, "port", process.env.PORT || "8081"), 10);
const PAIR = (arg(null, "pair", "") || cfg.pair || "EUR_USD").toUpperCase();

// The key, resolved exactly like the engine's configutil.py does.
function apiKey() {
  return (process.env.OANDA_API_KEY || dotenv.OANDA_API_KEY || cfg.api_key || "").trim();
}
const API_KEY = apiKey();

// ---- options --------------------------------------------------------------
const opts = {
  mock: MOCK,
  port: PORT,
  pair: PAIR,
  every: parseInt(arg(null, "every", cfg.stream ? cfg.stream.every : "60"), 10) || 60,
};

// Tracks why the engine isn't producing signals (missing numpy, no key, etc.)
let engineError = null;

// ---- engine subprocess ----------------------------------------------------
let child = null;
let restartCount = 0;
let lastRestartAt = 0;

function startEngine() {
  const args = ["signal.py", "stream", opts.pair];
  if (opts.mock) args.push("--mock");
  args.push("--every", String(opts.every));

  console.log(`[*] ${opts.mock ? "MOCK" : "real"} engine: python3 ${args.join(" ")}`);
  child = spawn("python3", args, { cwd: ROOT });

  child.stdout.setEncoding("utf8");
  let buf = "";
  child.stdout.on("data", (d) => {
    buf += d;
    let nl;
    while ((nl = buf.indexOf("\n")) !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) handleLine(line);
    }
  });

  child.stderr.on("data", (d) => {
    const s = String(d).trim();
    if (!s) return;
    console.error("[engine]", s);
    // Surface common startup failures instead of silently thrashing.
    if (/No module named|ModuleNotFoundError/.test(s)) {
      engineError = "Python engine missing a dependency (see: numpy). Run `pip install numpy`.";
    } else if (/No OANDA API key|api_key/i.test(s)) {
      engineError = "No OANDA API key — set OANDA_API_KEY in .env";
    }
  });

  child.on("exit", (code, sig) => {
    const now = Date.now();
    const wasQuick = (now - lastRestartAt) < 4000;
    // If the engine died almost instantly on startup (e.g. missing numpy or
    // the market is closed), don't thrash a restart loop forever.
    if (wasQuick && code !== 0 && restartCount >= 3 && !opts.mock) {
      const note = engineError || `engine exited on startup (code ${code})`;
      console.error(`[*] engine failed repeatedly: ${note}. Not restarting; fix and run again.`);
      engineError = `${note} — server is idle. Restart to retry.`;
      return;
    }
    const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(restartCount, 5)));
    if (now - lastRestartAt > delay) restartCount = 0;
    restartCount++;
    lastRestartAt = now;
    if (!opts.mock) console.error(`[*] engine exited (${code ?? sig}); restarting in ${delay}ms`);
    setTimeout(startEngine, delay);
  });
}

// ---- signal buffer --------------------------------------------------------
const signals = [];   // rolling buffer of {signal..., receivedAt}
const MAX_SIGNALS = 500;
let lastSignal = null;

function handleLine(line) {
  let obj;
  try {
    obj = JSON.parse(line);
  } catch {
    return; // ignore non-JSON (shouldn't happen in stream mode)
  }
  if (obj.type === "signal") {
    const rec = { ...obj, receivedAt: new Date().toISOString() };
    signals.push(rec);
    if (signals.length > MAX_SIGNALS) signals.shift();
    lastSignal = rec;
    broadcast(rec);
  } else if (obj.type === "ready") {
    // engine is up; no fan-out needed
  }
  // other types (status/error/tick) are informational — log quietly
}

// ---- SSE + WS fan-out -----------------------------------------------------
const sseClients = new Set();      // response objects
const wsClients = new Set();       // WebSocket instances

let wsImpl = null;
try {
  wsImpl = require("ws");
} catch {
  wsImpl = null;
}

function broadcast(rec) {
  const payload = JSON.stringify(rec);
  for (const res of sseClients) {
    try {
      res.write(`data: ${payload}\n\n`);
    } catch {}
  }
  if (wsImpl) {
    for (const ws of wsClients) {
      try {
        if (ws.readyState === wsImpl.OPEN) ws.send(payload);
      } catch {}
    }
  }
}

// ---- HTTP -----------------------------------------------------------------
const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

const rng = crypto.randomBytes(16).toString("hex");

function serveDashboard(req, res) {
  const html = dashboardHtml();
  res.writeHead(200, { "Content-Type": mime[".html"] });
  res.end(html);
}

function apiSignals(req, res) {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ count: signals.length, signals: signals.slice(-50) }));
}

function apiStatus(req, res) {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({
    ok: true,
    mock: opts.mock,
    pair: opts.pair,
    apiKeyConfigured: Boolean(API_KEY),
    engine: child ? "running" : "starting",
    engineError,
    uptimeProcess: process.uptime().toFixed(1),
    lastSignal,
  }));
}

function sse(req, res) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  res.write(`retry: 2000\n\n`);
  res.write(`data: {"type":"hello","id":"${rng}"}\n\n`);
  sseClients.add(res);
  req.on("close", () => sseClients.delete(res));
}

const server = http.createServer((req, res) => {
  const u = req.url.split("?")[0];
  if (u === "/" || u === "/index.html") return serveDashboard(req, res);
  if (u === "/api/signals") return apiSignals(req, res);
  if (u === "/api/status") return apiStatus(req, res);
  if (u === "/sse") return sse(req, res);
  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "not found" }));
});

// Attach WebSocket server to the same HTTP server, if `ws` is installed.
if (wsImpl) {
  const { WebSocketServer } = wsImpl;
  const wss = new WebSocketServer({ server });
  wss.on("connection", (ws) => {
    wsClients.add(ws);
    // Send the recent buffer so a new subscriber gets context immediately.
    for (const s of signals.slice(-20)) {
      try {
        if (ws.readyState === wsImpl.OPEN) ws.send(JSON.stringify(s));
      } catch {}
    }
    ws.on("message", (m) => {
      const t = String(m);
      if (t === '{"type":"ping"}' || t === "ping") {
        try { ws.send(JSON.stringify({ type: "pong" })); } catch {}
      }
    });
    ws.on("close", () => wsClients.delete(ws));
    ws.on("error", () => wsClients.delete(ws));
  });
  console.log(`[*] WebSocket enabled on ws://localhost:${opts.port}`);
} else {
  console.warn("[!] 'ws' package not installed. Run `npm install` to enable WebSocket. HTTP/SSE still work.");
}

server.listen(opts.port, () => {
  console.log(`[*] Dashboard   : http://localhost:${opts.port}/`);
  console.log(`[*] SSE feed    : http://localhost:${opts.port}/sse`);
  console.log(`[*] API signals : http://localhost:${opts.port}/api/signals`);
  console.log(`[*] API status  : http://localhost:${opts.port}/api/status`);
  console.log(`[*] Listening on http://localhost:${opts.port} (pair=${opts.pair}, mock=${opts.mock})`);
});

startEngine();

// ---- graceful shutdown ----------------------------------------------------
function shutdown() {
  console.log("\n[*] shutting down...");
  if (child) child.kill("SIGINT");
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1500).unref();
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

// ---- dashboard ------------------------------------------------------------
function dashboardHtml() {
  const keyNote = API_KEY
    ? `<span class="ok">● API key configured</span>`
    : `<span class="warn">● NO API KEY — set OANDA_API_KEY in .env</span>`;
  const engineNote = opts.mock
    ? `<span class="warn">● MOCK mode (synthetic signals)</span>`
    : `<span class="ok">● real engine</span>`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FX Signals — ${opts.pair.replace("_", "/")}</title>
<style>
  :root{--bg:#0f1115;--card:#1a1d24;--line:#2a2f3a;--txt:#e6e8ee;--dim:#8b93a3;
        --up:#16c784;--down:#f6465d;--acc:#4f8cff}
  *{box-sizing:border-box}
  body{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);color:var(--txt)}
  header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:14px;align-items:center}
  h1{font-size:16px;margin:0}
  .meta{font-size:12px;color:var(--dim);display:flex;flex-wrap:wrap;gap:10px;align-items:center}
  .ok{color:var(--up)} .warn{color:#ffb020}
  main{max-width:820px;margin:0 auto;padding:18px}
  .sig{border:1px solid var(--line);border-left:4px solid var(--dim);border-radius:8px;
       padding:12px 14px;margin-bottom:12px;background:var(--card);animation:in .25s ease}
  @keyframes in{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
  .sig.up{border-left-color:var(--up)} .sig.dn{border-left-color:var(--down)}
  .row{display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;align-items:center}
  .side{font-weight:700;font-size:15px;letter-spacing:.5px}
  .side.up{color:var(--up)} .side.dn{color:var(--down)}
  .tag{font-size:11px;padding:2px 7px;border-radius:4px;background:#222734;color:var(--dim)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:4px 18px;font-size:12.5px;color:var(--txt);margin-top:8px}
  .grid b{color:var(--dim);font-weight:500}
  .grid .v{color:var(--acc)}
  .reason{font-size:12px;color:var(--dim);margin-top:8px}
  .empty{color:var(--dim);text-align:center;padding:40px 0;font-size:13px}
  footer{color:#5a6270;font-size:11px;text-align:center;padding:18px}
</style>
</head>
<body>
<header>
  <h1>📈 ${opts.pair.replace("_", "/")} · Live Signals</h1>
  <div class="meta">
    <span>${keyNote}</span>
    <span>${engineNote}</span>
    <span id="conn">○ connecting…</span>
    <button id="clear" style="margin-left:auto;background:#222734;color:var(--txt);border:1px solid var(--line);border-radius:5px;padding:4px 10px;cursor:pointer">clear</button>
  </div>
</header>
<main id="list"><div class="empty">Waiting for the next closed-bar signal…</div></main>
<footer>Analysis only — you make the call. No auto-trading.</footer>
<script>
  const list=document.getElementById('list');const conn=document.getElementById('conn');
  const fmt=p=>p<10?p.toFixed(5):p.toFixed(4);
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function card(s){
    const up=s.side==='CALL';
    const el=document.createElement('div');el.className='sig '+(up?'up':'dn');
    const ind=s.indicators?Object.entries(s.indicators).filter(([k,v])=>v!=null).map(([k,v])=>'<span class="tag">'+esc(k)+'='+esc(v)+'</span>').join(' '):'';
    el.innerHTML=
      '<div class="row"><span class="side '+(up?'up':'dn')+'">'+(up?'▲':'▼')+' '+esc(s.side)+'</span>'+
      '<span class="tag">'+esc(s.model)+'</span>'+
      '<span class="tag">conf '+(s.confidence*100).toFixed(0)+'%</span></div>'+
      '<div class="grid">'+
        '<div><b>price</b> <span class="v">'+fmt(s.price)+'</span></div>'+
        '<div><b>expiry</b> ~'+esc(s.expiry_hint)+'m</div>'+
        '<div><b>close</b> '+esc(s.time)+'</div>'+
        (ind?'<div><b>ind</b> '+ind+'</div>':'')+
      '</div>'+
      '<div class="reason">'+esc((s.reasons||[]).join(' · '))+'</div>';
    list.prepend(el);
    while(list.children.length>40)list.lastChild.remove();
  }
  const es=new EventSource('/sse');
  es.onopen=()=>{conn.textContent='● live';conn.style.color='var(--up)'};
  es.onerror=()=>{conn.textContent='○ reconnecting';conn.style.color='#ffb020'};
  es.onmessage=e=>{try{const s=JSON.parse(e.data);if(s.type==='signal')card(s)}catch{}};
  document.getElementById('clear').onclick=()=>{list.innerHTML='<div class="empty">cleared — waiting for next signal…</div>'};
</script>
</body>
</html>`;
}
