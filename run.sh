#!/usr/bin/env bash
# All-in-one: clone this repo, check the .env, then just `bash run.sh` or `./run.sh`
# No pip installs, no numpy — the engine is 100% Python stdlib.
#
#   ./run.sh            start the live-signals server (server.js) on :8081
#   ./run.sh once       one signal for the configured pair, right now
#   ./run.sh scan       scan the whole watchlist once
#   ./run.sh stream     raw JSON-lines signal feed (no server)
set -e
cd "$(dirname "$0")"

# 1. API key
if ! grep -qs 'OANDA_API_KEY=.' .env 2>/dev/null && [ -z "$OANDA_API_KEY" ]; then
  echo "No OANDA_API_KEY found. Create .env from .env.example and add your key."
  echo "  cp .env.example .env  &&  nano .env"
  echo "(Free demo key: https://www.oanda.com/demo-account/)"
  exit 1
fi

# 2. Node deps (only needed for the server / WebSocket; HTTP+SSE need none)
if [ ! -d node_modules/ws ] && command -v npm >/dev/null 2>&1; then
  npm install >/dev/null 2>&1 || echo "npm install skipped (HTTP/SSE still work without it)"
fi

CMD="${1:-start}"
case "$CMD" in
  start)   exec node server.js ;;
  once)    shift; exec python3 signal.py once "$@" ;;
  scan)    shift; exec python3 signal.py scan "$@" ;;
  stream)  shift; exec python3 signal.py stream "$@" ;;
  check)   exec python3 signal.py check ;;
  mock)    exec node server.js --mock ;;
  *) echo "Usage: ./run.sh [start|once|scan|stream|check|mock]"; exit 1 ;;
esac
