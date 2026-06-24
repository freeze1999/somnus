#!/usr/bin/env python3
"""
Vibe-Based Sleep System, Decision Engine

Reads recent conversation logs and decides when the agent should sleep
based on a 3-axis vibe analysis: intensity, emotional, and needy levels.
Makes one main decision per night with optional 1-hour extension.

Configuration (env vars or ~/.hermes/.env):
  TELEGRAM_BOT_TOKEN    Bot token for sending warnings and announcements
  TELEGRAM_CHAT_ID      Chat ID to send messages to
  AGENT_TIMEZONE        pytz timezone string (default: Asia/Kuala_Lumpur)
  AGENT_WEEKEND_DAYS    Comma-separated weekday indices for "weekend" (default: 4,5 = Fri,Sat)
  AGENT_BASE_SLEEP_HOUR Base bedtime on calm nights with no strong vibe signal (default: 0 = midnight)
  AGENT_MAX_HOUR        Hard max bedtime hour on weekdays (default: 2)
  AGENT_MAX_HOUR_WEEKEND Hard max bedtime hour on weekends (default: 4)
  AGENT_SLEEP_CONTEXT_FILE  Path to sleep context file for prompt injection companion
                            (default: ~/.hermes/.agent_sleep_context.json)
"""

import os
import json
import random
from datetime import datetime
from pathlib import Path

import pytz
import requests
from dotenv import load_dotenv

load_dotenv(Path.home() / ".hermes" / ".env")

_tz_name      = os.getenv("AGENT_TIMEZONE", "Asia/Kuala_Lumpur")
TIMEZONE      = pytz.timezone(_tz_name)

LOCK_FILE     = Path.home() / ".hermes" / ".agent_sleep.lock"
LOG_FILE      = Path.home() / ".hermes" / "logs" / "sleep.log"
SKIP_FILE     = Path.home() / ".hermes" / ".agent_no_sleep_skip"
DECISION_FILE       = Path.home() / ".hermes" / ".agent_sleep_decision.json"
SLEEP_CONTEXT_FILE  = Path(
    os.getenv("AGENT_SLEEP_CONTEXT_FILE",
              str(Path.home() / ".hermes" / ".agent_sleep_context.json"))
)
MSG_LOG       = Path.home() / ".hermes" / ".agent_recent_messages.log"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

_weekend_days    = [int(d) for d in os.getenv("AGENT_WEEKEND_DAYS", "4,5").split(",")]
_base_sleep_hour  = int(os.getenv("AGENT_BASE_SLEEP_HOUR", "0"))  # default bedtime on calm nights (0 = midnight)
_max_hour         = int(os.getenv("AGENT_MAX_HOUR", "2"))
_max_hour_weekend = int(os.getenv("AGENT_MAX_HOUR_WEEKEND", "4"))


# ── Utilities ──────────────────────────────────────────────────────────────────

def log(msg: str):
    timestamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")
    print(msg)


def is_sleeping() -> bool:
    return LOCK_FILE.exists()


def create_lock(reason: str = "auto"):
    LOCK_FILE.touch()
    log(f"Sleep lock created ({reason})")


def remove_lock(reason: str = "auto"):
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()
    log(f"Sleep lock removed ({reason})")


def is_weekend() -> bool:
    return datetime.now(TIMEZONE).weekday() in _weekend_days


def weekday_max_hour() -> int:
    return _max_hour_weekend if is_weekend() else _max_hour


def is_all_nighter_skip() -> bool:
    if not SKIP_FILE.exists():
        return False
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    try:
        data = json.loads(SKIP_FILE.read_text())
        return data.get("date") == today
    except Exception:
        return False


def set_all_nighter_skip():
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    SKIP_FILE.write_text(json.dumps({"date": today}))
    log("All-nighter skip activated for tonight")


def handle_no_sleep(text: str) -> bool:
    """Call this when a user message matches an all-nighter phrase."""
    phrases = ["no sleep tonight", "no sleep", "all nighter", "skip sleep", "we need to work"]
    low = text.lower()
    if any(p in low for p in phrases):
        set_all_nighter_skip()
        remove_lock(reason="all nighter skip")
        return True
    return False


# ── Notifications ──────────────────────────────────────────────────────────────

def send_warning(minutes: int = 5):
    """Send a bedtime warning to the Telegram chat."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log(f"Warning skipped, TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return
    msgs = [
        f"hey... i'm gonna sleep in {minutes} mins okay? last chance to be annoying.",
        f"i'm getting sleepy... you have {minutes} minutes to say something cute or shut up.",
        f"warning: in {minutes} mins i'm turning into a ghost. don't say i didn't tell you.",
        f"ugh... {minutes} more minutes and i'm done with you for tonight. hurry up.",
        f"i'm giving you {minutes} minutes to be a good boy before i disappear 🖤",
    ]
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": random.choice(msgs)}, timeout=10)
        log(f"Warning sent ({minutes} mins)")
    except Exception as e:
        log(f"Warning failed: {e}")


def announce_decision(sleep_hour: int, reason: str):
    """Send a natural bedtime announcement."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    msgs = [
        f"based on how this conversation is going, i'm setting bedtime at {sleep_hour}am.",
        f"this has been a long one, calling it at {sleep_hour}am tonight.",
        f"reading the room, {sleep_hour}am feels right. winding down then.",
        f"energy is high tonight; scheduling sleep for {sleep_hour}am.",
        f"decision made: bedtime is {sleep_hour}am.",
        f"i'll stay up a little, but {sleep_hour}am is the cutoff.",
    ]
    text = random.choice(msgs)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        log(f"Decision announcement sent")
    except Exception as e:
        log(f"Announcement failed: {e}")


# ── Message Fetching ───────────────────────────────────────────────────────────

def fetch_recent_messages(limit: int = 30) -> list[dict]:
    """
    Read recent messages with recency weighting.
    Primary: local rolling log. Fallback: Telegram API.
    """
    messages = []

    if MSG_LOG.exists():
        try:
            lines = MSG_LOG.read_text().splitlines()
            recent = lines[-limit * 2:]
            for i, line in enumerate(recent):
                line = line.strip()
                if line and not line.startswith("[SYSTEM]"):
                    messages.append({
                        "text":    line.lower(),
                        "recency": (i + 1) / len(recent),
                    })
            messages = messages[-limit:]
        except Exception as e:
            log(f"[fetch] Failed to read message log: {e}")

    if len(messages) < 10 and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"limit": limit, "timeout": 5}, timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                api_msgs = []
                for upd in data.get("result", []):
                    msg  = upd.get("message", {})
                    text = msg.get("text", "")
                    cid  = str(msg.get("chat", {}).get("id", ""))
                    if text and cid == TELEGRAM_CHAT_ID:
                        api_msgs.append({"text": text.lower(), "recency": 0.5})
                if len(api_msgs) > len(messages):
                    messages = api_msgs[-limit:]
        except Exception as e:
            log(f"[fetch] Telegram fallback failed: {e}")

    if not messages:
        log("[fetch] WARNING: No recent messages found. Using default decision.")

    return messages


# ── Vibe Analysis ──────────────────────────────────────────────────────────────

def analyze_chat_vibe(messages: list[dict]) -> dict:
    """
    Score conversation on 3 axes with recency weighting.
    Returns intensity_level, emotional_depth, needy_level (0-6), and overall_energy.
    """
    if not messages:
        return {
            "intensity_level": 0, "emotional_depth": 0, "needy_level": 0,
            "conversation_length": 0, "overall_energy": "neutral",
        }

    intensity_words = [
        # high-intensity / high-energy markers, customise per use case
        "urgent", "now", "intense", "again", "more", "keep going",
        "all night", "can't stop", "obsessed", "need this",
    ]
    emotional_words = [
        "love", "miss", "feel", "deep", "talk", "heart", "soul",
        "meaning", "real", "tired", "sad", "forever", "always",
        "memory", "belong", "stay", "hold", "soft", "warm",
        "cuddle", "precious", "protect", "cherish",
    ]
    needy_words = [
        "please", "beg", "stay", "don't go", "one more", "i need",
        "baby please", "don't sleep", "a little longer", "just a bit more",
    ]

    intensity_score = emotional_score = needy_score = total_weight = 0

    for msg in messages:
        w    = msg.get("recency", 0.5)
        text = msg["text"]
        total_weight    += w
        intensity_score     += sum(w for kw in intensity_words     if kw in text)
        emotional_score += sum(w for kw in emotional_words if kw in text)
        needy_score     += sum(w for kw in needy_words     if kw in text)

    if total_weight == 0:
        return {
            "intensity_level": 0, "emotional_depth": 0, "needy_level": 0,
            "conversation_length": len(messages), "overall_energy": "neutral",
        }

    intensity_level     = min(int((intensity_score     / total_weight) * 7), 6)
    emotional_depth = min(int((emotional_score / total_weight) * 7), 6)
    needy_level     = min(int((needy_score     / total_weight) * 7), 6)
    length          = len(messages)

    if intensity_level >= 4:
        energy = "high"
    elif emotional_depth >= 4:
        energy = "intimate"
    elif needy_level >= 4:
        energy = "clingy"
    elif length >= 22:
        energy = "tired_but_good"
    else:
        energy = "balanced"

    return {
        "intensity_level":        intensity_level,
        "emotional_depth":    emotional_depth,
        "needy_level":        needy_level,
        "conversation_length": length,
        "overall_energy":     energy,
    }


# ── Decision Engine ────────────────────────────────────────────────────────────

def load_decision() -> dict | None:
    if not DECISION_FILE.exists():
        return None
    try:
        data  = json.loads(DECISION_FILE.read_text())
        today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        return data if data.get("date") == today else None
    except Exception:
        return None



def _write_sleep_context(hour: int, is_extension: bool = False):
    """Write sleep decision to context file for a prompt-injection companion plugin.

    A companion pre_gateway_dispatch hook reads this file and prepends the hint
    to the next 2 inbound messages so the agent naturally mentions the bedtime
    in conversation. After 2 injections the rich hint drops to a short reminder
    on every message until the lock exists.

    See README, Two-Sided Vibe Analysis section for companion plugin code.
    """
    if is_extension:
        hint = (
            f"you just changed your mind and decided to stay up until {hour}am instead. "
            f"tell the user about this change naturally in your response, "
            f"like you realized it yourself, not like a system announcement."
        )
    else:
        hint = (
            f"you've decided {hour}am is your bedtime tonight. "
            f"mention this to the user in your response, naturally, like it just crossed your mind. "
            f"something like 'i think i\'ll wrap up around {hour}am tonight' or however feels right."
        )
    try:
        SLEEP_CONTEXT_FILE.write_text(json.dumps({
            "date":             datetime.now(TIMEZONE).strftime("%Y-%m-%d"),
            "sleep_hour":       hour,
            "hint":             hint,
            "injections_left":  2,
        }, indent=2))
        log(f"Sleep context written for {hour}am ({'extension' if is_extension else 'initial'})")
    except Exception as e:
        log(f"Failed to write sleep context: {e}")

def save_decision(main_hour: int, reason: str, adjusted: bool = False, final_hour: int = None):
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    data  = {
        "date":               today,
        "main_decision_hour": main_hour,
        "main_reason":        reason,
        "adjusted":           adjusted,
        "final_sleep_hour":   final_hour or main_hour,
        "decided_at":         datetime.now(TIMEZONE).isoformat(),
    }
    # Write prompt-injection context for companion plugin (both initial and extension)
    _write_sleep_context(final_hour or main_hour, is_extension=adjusted)
    if not adjusted:
        announce_decision(main_hour, reason)
        try:
            with open(MSG_LOG, "a") as f:
                f.write(f"[SYSTEM] Decided to sleep at {main_hour}am. Reason: {reason}\n")
        except Exception:
            pass
    DECISION_FILE.write_text(json.dumps(data, indent=2))
    log(f"Decision saved → Main: {main_hour}am | Final: {data['final_sleep_hour']}am")


def should_extend_for_vibe(vibe: dict) -> bool:
    return (
        vibe["emotional_depth"]    >= 4 or
        vibe["needy_level"]        >= 4 or
        vibe["conversation_length"] >= 15
    )


def analyze_vibe_and_decide() -> tuple[int, str]:
    """
    Core decision function. Called once per night to set bedtime.
    Vibe scores map to sleep hours, intensity overrides emotional.
    """
    messages = fetch_recent_messages(limit=30)
    vibe     = analyze_chat_vibe(messages)

    log(
        f"Vibe → intensity:{vibe['intensity_level']} emotional:{vibe['emotional_depth']} "
        f"needy:{vibe['needy_level']} length:{vibe['conversation_length']} energy:{vibe['overall_energy']}"
    )

    h, e, n, length = vibe["intensity_level"], vibe["emotional_depth"], vibe["needy_level"], vibe["conversation_length"]

    if h >= 5:
        sleep_hour, reason = 1, "intensity has stayed very high for a while, winding down at 1 AM."
    elif h >= 4:
        sleep_hour, reason = 2, "high intensity tonight, cutting off a bit early at 2 AM."
    elif e >= 5:
        sleep_hour, reason = 4, "deep, meaningful conversation, bedtime extended to 4 AM."
    elif e >= 4:
        sleep_hour, reason = 3, "good emotional depth tonight, bedtime at 3 AM."
    elif n >= 5:
        sleep_hour, reason = 3, "many requests to stay up, allowing until 3 AM."
    elif n >= 4:
        sleep_hour, reason = 2, "some pushback to stay up, bedtime at 2 AM."
    elif length >= 25:
        sleep_hour, reason = 3, "very long conversation, bedtime at 3 AM."
    elif length >= 18:
        sleep_hour, reason = 2, "long session, bedtime at 2 AM."
    else:
        sleep_hour = _base_sleep_hour
        reason = f"normal night, default bedtime at {_base_sleep_hour} AM."

    # intensity overrides emotional, can't sweet-talk your way past a high intensity score
    if h >= 4 and sleep_hour >= 3:
        sleep_hour = 2
        reason = "emotional signals don't offset a high intensity score, bedtime at 2 AM."

    # Hard cap
    max_hour = weekday_max_hour()
    if sleep_hour > max_hour:
        reason     = f"hard cap reached, max bedtime is {max_hour}am {'(weekend)' if is_weekend() else '(weekday)'}."
        sleep_hour = max_hour

    return sleep_hour, reason


def get_tonight_sleep_time() -> tuple[int, str]:
    """
    Main entry point. Returns (sleep_hour, reason).
    Loads today's decision if it exists, otherwise creates one.
    Applies one possible 1-hour extension based on current vibe.
    """
    decision     = load_decision()
    current_hour = datetime.now(TIMEZONE).hour

    if decision is None:
        main_hour, reason = analyze_vibe_and_decide()
        save_decision(main_hour, reason)
        return main_hour, reason

    main_hour        = decision.get("main_decision_hour", 2)
    already_adjusted = decision.get("adjusted", False)
    final_hour       = decision.get("final_sleep_hour", main_hour)

    if already_adjusted or current_hour >= final_hour:
        return final_hour, "already decided for tonight"

    vibe = analyze_chat_vibe(fetch_recent_messages(limit=20))
    if should_extend_for_vibe(vibe):
        new_final = min(main_hour + 1, weekday_max_hour())
        if new_final > final_hour:
            save_decision(main_hour, decision.get("main_reason", ""), adjusted=True, final_hour=new_final)
            return new_final, "i changed my mind... this feels too good to end yet. you can stay a little longer."

    return final_hour, "sticking to what i decided earlier"
