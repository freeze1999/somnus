#!/usr/bin/env python3
"""
Sleep Context Injection, Companion Hermes Plugin

Reads the sleep context file written by sleep_system.py and prepends
a bedtime hint to the agent's inbound messages so it naturally mentions
the decided bedtime in conversation.

Install:
  cp companion_time_inject.py ~/.hermes/plugins/sleep-context-inject/__init__.py
  mkdir -p ~/.hermes/plugins/sleep-context-inject
  # add a plugin.yaml (see below)

plugin.yaml:
  name: sleep-context-inject
  version: 1.0.0
  hooks:
    - pre_gateway_dispatch

The sleep context file (~/.hermes/.agent_sleep_context.json) is written
automatically by sleep_system.py when a bedtime decision is made.

Injection behaviour:
  - First 2 messages after a decision: full hint telling the agent to mention
    the bedtime naturally in its response
  - After that: the context file is deleted, no further injection.
    Two reminders is enough; the agent has already acknowledged its bedtime.

Configuration (env vars or ~/.hermes/.env):
  AGENT_SLEEP_CONTEXT_FILE  Path to context file (default: ~/.hermes/.agent_sleep_context.json)
"""

import json
import os
from datetime import date, datetime
from pathlib import Path

_CONTEXT_FILE = Path(
    os.getenv("AGENT_SLEEP_CONTEXT_FILE",
              str(Path.home() / ".hermes" / ".agent_sleep_context.json"))
)


def _get_sleep_hint() -> str:
    """Read context file, return the rich hint, and decrement the counter.

    Injects exactly twice, then deletes the context file so nothing more is
    injected for the rest of the night. The hint already tells the agent to
    mention its bedtime, so two reminders is enough, no permanent nagging.
    """
    try:
        if not _CONTEXT_FILE.exists():
            return ""

        data = json.loads(_CONTEXT_FILE.read_text())
        if data.get("date") != str(date.today()):
            _CONTEXT_FILE.unlink(missing_ok=True)
            return ""

        left = int(data.get("injections_left", 0))
        if left <= 0:
            _CONTEXT_FILE.unlink(missing_ok=True)
            return ""

        hint = data.get("hint", "")
        data["injections_left"] = left - 1
        if data["injections_left"] <= 0:
            _CONTEXT_FILE.unlink(missing_ok=True)
        else:
            _CONTEXT_FILE.write_text(json.dumps(data, indent=2))
        return hint
    except Exception:
        return ""


def register(ctx) -> None:
    log = getattr(ctx, "logger", None)

    def _inject_sleep_context(event=None, **kwargs):
        if event is None:
            return None
        text = getattr(event, "text", "") or ""
        if not text:
            return None

        hint = _get_sleep_hint()
        if not hint:
            return None  # nothing to inject, let other hooks handle normally

        new_text = f"[\U0001f4ad {hint}] {text}"
        if log:
            try:
                log.info("[sleep-context-inject] injected bedtime hint")
            except Exception:
                pass
        return {"action": "rewrite", "text": new_text}

    ctx.register_hook("pre_gateway_dispatch", _inject_sleep_context)
    if log:
        try:
            log.info("[sleep-context-inject] registered")
        except Exception:
            pass
