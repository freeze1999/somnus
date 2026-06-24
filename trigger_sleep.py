#!/usr/bin/env python3
"""
Sleep Trigger, runs every minute via cron.

Manages the full sleep/wake lifecycle:
  1. Cleans stale locks left from previous days
  2. Skips if all-nighter mode is active
  3. Removes lock during daytime hours (8am, 2pm, capped at AGENT_FORCE_SLEEP_HOUR)
  4. Creates lock when the decided bedtime hour arrives

Install:
  * * * * * cd /path/to/scripts && python3 trigger_sleep.py >> /path/to/sleep.log 2>&1
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz

# Allow running from any directory
sys.path.insert(0, str(Path(__file__).parent))

from sleep_system import (
    LOCK_FILE, TIMEZONE,
    create_lock, remove_lock,
    get_tonight_sleep_time,
    is_all_nighter_skip,
    is_sleeping,
    log,
    send_warning,
)

_WAKE_START       = int(os.getenv("AGENT_WAKE_HOUR_START",  "8"))   # remove lock after this hour
_WAKE_END         = int(os.getenv("AGENT_WAKE_HOUR_END",    "23"))  # stop auto-waking after this hour
_FORCE_SLEEP_HOUR = int(os.getenv("AGENT_FORCE_SLEEP_HOUR", "14"))  # must match force_wake.py -- prevents fighting the afternoon re-lock


def is_lock_stale() -> bool:
    """Lock is stale if it was created on a previous calendar day."""
    if not is_sleeping():
        return False
    lock_mtime = datetime.fromtimestamp(LOCK_FILE.stat().st_mtime, tz=TIMEZONE)
    return lock_mtime.date() < datetime.now(TIMEZONE).date()


def is_daytime() -> bool:
    """True only during the safe auto-wake window.
    Caps at AGENT_FORCE_SLEEP_HOUR so this script does not fight
    force_wake.py's afternoon re-lock. Both scripts must share the
    same AGENT_FORCE_SLEEP_HOUR value (default 14).
    """
    hour = datetime.now(TIMEZONE).hour
    return _WAKE_START <= hour < min(_WAKE_END, _FORCE_SLEEP_HOUR)


def main():
    # 1. Stale lock cleanup
    if is_lock_stale():
        remove_lock(reason="stale lock from previous day")
        return

    # 2. All-nighter skip
    if is_all_nighter_skip():
        log("All-nighter mode active. Not sleeping tonight.")
        return

    # 3. Wake cycle
    if is_sleeping() and is_daytime():
        remove_lock(reason=f"daytime wake ({_WAKE_START}am, {min(_WAKE_END, _FORCE_SLEEP_HOUR)}pm)")
        return

    # 4. Already sleeping, nothing to do
    if is_sleeping():
        log("Already sleeping. Skipping.")
        return

    # 5. Check if it's bedtime
    now          = datetime.now(TIMEZONE)
    current_hour = now.hour
    sleep_hour, reason = get_tonight_sleep_time()
    log(f"Tonight's bedtime: {sleep_hour}:00, {reason}")

    if current_hour >= sleep_hour and current_hour < 5:
        send_warning(minutes=1)
        time.sleep(30)
        create_lock(reason=f"vibe decision: {reason}")
    else:
        log(f"Not bedtime yet (decided {sleep_hour}:00, current {current_hour}:00)")


if __name__ == "__main__":
    main()
