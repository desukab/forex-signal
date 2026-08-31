"""Notification + automation hooks (Telegram push and JSON webhook).

Both are optional and degrade to no-ops when their config is empty, so the
CLI works out of the box. HTTP is done with the standard library (urllib) to
keep the install footprint tiny on Termux.

  * Telegram: pushes a formatted line to a chat via a bot token. Great when
    the operator is away from the terminal.
  * Webhook: POSTs the full JSON payload of a Signal to a URL — the
    automation-ready seam for a future auto-execution server (this build only
    *reports*; it never places a trade itself).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
import urllib.error

from engine.configutil import get, CFG
from engine.registry import Signal


def _quiet(event: str, **kwargs):
    """No-op; used when a channel isn't configured."""
    return False


def telegram_send(message: str, cfg: dict = CFG) -> bool:
    """Send a plain message via a bot. True if sent, False if not configured."""
    token = get(cfg, "telegram.bot_token", "") or ""
    chat = get(cfg, "telegram.chat_id", "") or ""
    if not token or not chat:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": message,
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except urllib.error.URLError:
        return False


def webhook_send(signal: Signal, cfg: dict = CFG) -> bool:
    """POST a Signal's JSON payload to a configured webhook URL. True if sent."""
    url = get(cfg, "webhook.url", "") or ""
    enabled = bool(get(cfg, "webhook.enabled", False) and url)
    if not enabled:
        return False
    data = signal.to_json().encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except urllib.error.URLError:
        return False


def push_signal(signal: Signal, cfg: dict = CFG) -> dict:
    """Route a signal to every configured channel; return which fired."""
    results = {"telegram": False, "webhook": False}
    if telegram_send(_format_telegram(signal), cfg):
        results["telegram"] = True
    if webhook_send(signal, cfg):
        results["webhook"] = True
    return results


def _format_telegram(s: Signal) -> str:
    emoji = "🟢" if s.side == "CALL" else "🔴"
    sym = s.symbol.replace("_", "/")
    return (
        f"<b>{emoji} {s.side} {sym}</b>\n"
        f"Model     : {s.model}\n"
        f"Confidence: {s.confidence:.0%}\n"
        f"Price     : {s.price}\n"
        f"Expiry    : ~{s.expiry_hint}m\n"
        f"Close bar : {s.time}\n"
        f"Setup     : {', '.join(s.reasons)}"
    )
