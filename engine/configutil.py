"""Central configuration loading for the v2 signal engine.

Loads config.json (in the project root), overlays environment variables
(.env or shell), and exposes typed getters. Keeps a single source of truth.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
WATCHLIST_PATH = ROOT / "watchlist.json"
ENV_PATH = ROOT / ".env"


def _load_dotenv():
    """Load KEY=VALUE lines from .env into os.environ if present (no deps)."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_config() -> dict:
    """Return the full config dict (merged, never mutated by callers)."""
    _load_dotenv()
    cfg = json.loads(CONFIG_PATH.read_text())
    # Environment overrides for secrets / runtime.
    if os.environ.get("OANDA_API_KEY"):
        cfg["api_key"] = os.environ["OANDA_API_KEY"]
    if os.environ.get("OANDA_ACCOUNT_TYPE"):
        cfg["account_type"] = os.environ["OANDA_ACCOUNT_TYPE"]
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram"]["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg["telegram"]["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
    if os.environ.get("WEBHOOK_URL"):
        cfg["webhook"]["url"] = os.environ["WEBHOOK_URL"]
        cfg["webhook"]["enabled"] = bool(cfg["webhook"]["url"])
    return cfg


def load_watchlist() -> list[dict]:
    """Return the pair list from watchlist.json (empty list if absent)."""
    if not WATCHLIST_PATH.exists():
        return []
    return json.loads(WATCHLIST_PATH.read_text()).get("pairs", [])


def get(cfg: dict, dotted: str, default=None):
    """Fetch a value by dotted path, e.g. get(cfg, 'momentum.ema_fast')."""
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


CFG = load_config()
