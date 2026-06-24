#!/usr/bin/env python3
"""
Hermes Sleep Gate Plugin

Blocks inbound messages while the agent is "asleep" (lock file present) and
replies with a zero-token asleep message. Also logs message text for optional
vibe/sentiment analysis. Runs as a pre_gateway_dispatch hook.

Configuration (env vars or ~/.hermes/.env):
  AGENT_WAKE_PHRASE      Secret phrase to immediately clear the sleep lock.
                         *** CHANGE THIS before deploying, default is disabled ***
  AGENT_SLEEP_MSG        Message sent when agent is sleeping.
                         Default: "Agent is currently asleep."
  AGENT_NO_SLEEP_PHRASES Comma-separated phrases that let a message through without
                         clearing the lock permanently (all-nighter overrides).
                         Default: "no sleep tonight,all nighter,skip sleep,we need to work"

Required bot tokens (read from env or ~/.hermes/.env):
  DISCORD_BOT_TOKEN
  TELEGRAM_BOT_TOKEN
"""

import json
import os
import time
import urllib.request
from pathlib import Path

LOCK_FILE  = Path.home() / ".hermes" / ".agent_sleep.lock"
SKIP_FILE  = Path.home() / ".hermes" / ".agent_no_sleep_skip"
DEDUP_FILE = Path.home() / ".hermes" / ".agent_sleep_dedup.json"
RECENT_LOG = Path.home() / ".hermes" / ".agent_recent_messages.log"
ENV_PATH   = Path.home() / ".hermes" / ".env"

_DISCORD_UA     = "DiscordBot (https://github.com/Rapptz/discord.py, 2.0.0)"
_DEFAULT_NO_SLEEP = "no sleep tonight,all nighter,skip sleep,we need to work"


def _env():
    out = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    # env vars override .env file
    for key in ("DISCORD_BOT_TOKEN", "TELEGRAM_BOT_TOKEN",
                "AGENT_WAKE_PHRASE", "AGENT_SLEEP_MSG", "AGENT_NO_SLEEP_PHRASES"):
        if key in os.environ:
            out[key] = os.environ[key]
    return out


def _send_telegram(chat, text, env):
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token or not chat:
        return
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": str(chat), "text": text}).encode()
    req  = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _send_discord(channel, text, env):
    token = env.get("DISCORD_BOT_TOKEN")
    if not token or not channel:
        return
    url  = f"https://discord.com/api/v10/channels/{channel}/messages"
    body = json.dumps({"content": text}).encode()
    req  = urllib.request.Request(url, data=body, headers={
        "Content-Type":  "application/json",
        "Authorization": f"Bot {token}",
        "User-Agent":    _DISCORD_UA,
    })
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _log_message(text):
    try:
        lines = RECENT_LOG.read_text().splitlines() if RECENT_LOG.exists() else []
        lines.append(f"in: {text.lower()[:200]}")
        if len(lines) > 200:
            lines = lines[-200:]
        RECENT_LOG.write_text("\n".join(lines) + "\n")
    except Exception:
        pass


def _already_handled(key):
    now  = time.time()
    seen = {}
    if DEDUP_FILE.exists():
        try:
            seen = json.loads(DEDUP_FILE.read_text())
        except Exception:
            seen = {}
    seen = {k: v for k, v in seen.items() if now - v < 60}
    hit       = key in seen
    seen[key] = now
    try:
        DEDUP_FILE.write_text(json.dumps(seen))
    except Exception:
        pass
    return hit


def register(ctx):
    log = getattr(ctx, "logger", None)

    # Cache env at startup, avoids a disk read on every message.
    # Hermes restarts the plugin on config changes so this is safe.
    _cached_env = _env()
    wake_phrase = _cached_env.get("AGENT_WAKE_PHRASE", "").strip().lower()
    sleep_msg   = _cached_env.get("AGENT_SLEEP_MSG", "Agent is currently asleep.")
    no_sleep    = [
        p.strip().lower()
        for p in _cached_env.get("AGENT_NO_SLEEP_PHRASES", _DEFAULT_NO_SLEEP).split(",")
        if p.strip()
    ]

    def _sleep_gate(event=None, gateway=None, session_store=None, **kwargs):
        if event is None:
            return None

        text       = getattr(event, "text", "") or ""
        src        = getattr(event, "source", None)
        platform   = ""
        chat_id    = None
        message_id = getattr(event, "message_id", None)

        if src is not None:
            p        = getattr(src, "platform", None)
            platform = str(getattr(p, "value", p) or "").lower()
            chat_id  = getattr(src, "chat_id", None)

        if text and platform in ("discord", "telegram"):
            _log_message(text)

        low = text.lower()

        # Emergency wake, clear lock and write skip so trigger_sleep doesn't re-lock
        if wake_phrase and wake_phrase in low:
            if LOCK_FILE.exists():
                try:
                    LOCK_FILE.unlink()
                except Exception:
                    pass
            try:
                import json as _json
                from datetime import datetime as _dt
                today = _dt.now().strftime("%Y-%m-%d")
                SKIP_FILE.write_text(_json.dumps({"date": today}))
            except Exception:
                pass
            return None

        # All-nighter override, let through without waking permanently
        if any(p in low for p in no_sleep):
            return None

        # Not sleeping, normal dispatch
        if not LOCK_FILE.exists():
            return None

        # Asleep: send one reply per unique message then skip
        key = message_id or f"{chat_id}:{text[:50]}:{int(time.time() // 10)}"
        if not _already_handled(key):
            if platform == "telegram":
                _send_telegram(chat_id, sleep_msg, _cached_env)
            elif platform == "discord":
                _send_discord(chat_id, sleep_msg, _cached_env)
            if log:
                try:
                    log.info(f"[sleep-gate] blocked {platform} msg, sent sleep reply")
                except Exception:
                    pass

        return {"action": "skip", "reason": "agent is sleeping"}

    ctx.register_hook("pre_gateway_dispatch", _sleep_gate)
    if log:
        try:
            log.info("[sleep-gate] registered pre_gateway_dispatch hook")
        except Exception:
            pass
