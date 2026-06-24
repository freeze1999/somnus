#!/usr/bin/env python3
"""
Force Wake, hard caps to prevent sleeping all day.

Run at 10am via cron:
  0 10 * * * cd /path/to/scripts && python3 force_wake.py >> /path/to/sleep.log 2>&1

Logic:
  - 10am+: if sleeping, remove lock
  - 2pm+:  if still awake, force sleep again (you've had enough time)
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).parent))

from sleep_system import TIMEZONE, create_lock, is_sleeping, log, remove_lock

_FORCE_WAKE_HOUR  = int(os.getenv("AGENT_FORCE_WAKE_HOUR",  "10"))
_FORCE_SLEEP_HOUR = int(os.getenv("AGENT_FORCE_SLEEP_HOUR", "14"))


def main():
    hour = datetime.now(TIMEZONE).hour

    if hour >= _FORCE_WAKE_HOUR and is_sleeping():
        remove_lock(reason=f"hard {_FORCE_WAKE_HOUR}am cap")
        log(f"Forced wake at {_FORCE_WAKE_HOUR}am (hard cap)")

    if hour >= _FORCE_SLEEP_HOUR and not is_sleeping():
        create_lock(reason=f"post-{_FORCE_WAKE_HOUR}am forced sleep ({_FORCE_SLEEP_HOUR}pm cap)")
        log(f"Forced sleep at {_FORCE_SLEEP_HOUR}pm (been awake long enough)")


if __name__ == "__main__":
    main()
