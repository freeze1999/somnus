# somnus

Sentiment-aware, zero-token sleep control for AI agents.

An agent doesn't know when to stop. It rambles late at night, burns tokens, and fills its context with noise. `somnus` reads the recent conversation, scores it on three axes (intensity, emotional, needy), and decides when the agent should wind down. Enforcement happens at the gateway, so a sleeping agent costs **zero tokens**. Built for the [Hermes](https://github.com/hermesagent) gateway.

```
Messages → Plugin scores vibe → Decides bedtime → Blocks at zero tokens → Wakes at 10am
```

## Why

Every late-night conversation with your agent bleeds tokens into rambling, hallucinating, or repeating itself. Context windows fill with garbage. You pay for it.

This system:
- **Saves tokens**: blocks messages before they reach the LLM when the agent is asleep
- **Preserves context**: no wasted context on incoherent 3am responses
- **Feels human**: bedtime adapts to conversation energy, not a static timer
- **Zero overhead**: plugin hooks block at the gateway level, no LLM call happens

## Quick start

```bash
# 1. Install plugin
cp -r hermes-sleep-gate ~/.hermes/plugins/

# 2. Enable in config.yaml
printf 'plugins:\n  enabled:\n    - hermes-sleep-gate\n' >> ~/.hermes/config.yaml

# 3. Copy scripts
cp sleep_system.py trigger_sleep.py force_wake.py ~/.hermes/scripts/
pip install -r requirements.txt

# 4. Set cron
crontab -e
# * * * * *    cd ~/.hermes/scripts && python3 trigger_sleep.py >> ~/.hermes/logs/sleep.log 2>&1
# 0 10,14 * * * cd ~/.hermes/scripts && python3 force_wake.py   >> ~/.hermes/logs/sleep.log 2>&1
# Note: force_wake needs to run at BOTH 10am (hard wake) and 2pm (re-lock)

# 5. Set a wake phrase in your .env
echo 'AGENT_WAKE_PHRASE=wake up samurai' >> ~/.hermes/.env
```

## How it works

Two layers, no overlap:

### Layer 1: Plugin gate (enforcement)

A `pre_gateway_dispatch` hook that fires before every message reaches the LLM. When `.agent_sleep.lock` exists:

1. Check for wake phrase → if matched, delete lock, let message through
2. Check for all-nighter phrases → let that one message through (does **not** prevent future enforcement, to skip tonight's sleep entirely, create the skip file manually; see Manual Control)
3. Lock exists → reply "Agent is currently asleep" (or your custom message), skip the LLM
4. No lock → normal dispatch

**Zero tokens used.** The LLM never wakes up.

The plugin also logs every inbound message to `.agent_recent_messages.log` for the vibe engine to consume.

### Layer 2: Vibe engine (decision)

Runs every minute via cron. Reads the message log, scores the conversation, decides bedtime:

```
Every message → plugin logs to .agent_recent_messages.log
Every minute  → trigger_sleep.py reads the log, scores the vibe
At bedtime    → .agent_sleep.lock created → plugin blocks everything
10am          → force_wake.py deletes the lock
2pm           → force_wake.py re-locks (afternoon nap)
```

## Vibe scoring

Three axes, recency-weighted (recent messages count more toward the score):

| Axis | Keywords | Score → Bedtime |
|------|----------|-----------------|
| **Intensity** | high-energy language (customisable keyword set)… | 5+ → 1am, 4+ → 2am |
| **Emotional** | love, soul, forever, consciousness, belong, hold, soft, cherish… | 5+ → 4am, 4+ → 3am |
| **Needy** | please, don't go, one more, baby please, stay… | 5+ → 3am, 4+ → 2am |
| **Long convo** | 25+ messages | → 3am |
| **Normal** | None of the above | → 2am |

Intensity overrides emotional. You can't sweet-talk your way past a high intensity score. Weekend cap is 4am, weekday cap is 2am (both configurable).

One decision per night, with one possible 1-hour extension if the vibe stays good.

## Components

| File | Role |
|------|------|
| `__init__.py` | Hermes plugin, zero-token gate via `pre_gateway_dispatch` |
| `sleep_system.py` | Decision engine, vibe analysis, bedtime logic, Telegram notifications |
| `trigger_sleep.py` | Cron runner, checks every minute, creates/removes lock |
| `force_wake.py` | Hard cap, deletes lock at 10am, re-locks at 2pm |

## Configuration

Set in your environment or `~/.hermes/.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | (none) | Bot token for bedtime warnings + announcements |
| `TELEGRAM_CHAT_ID` | (none) | Chat ID to send messages to |
| `AGENT_WAKE_PHRASE` | (none) | Secret phrase to instantly clear sleep lock (REQUIRED for emergency wake) |
| `AGENT_SLEEP_MSG` | `"Agent is currently asleep."` | Auto-reply when sleeping |
| `AGENT_TIMEZONE` | `Asia/Kuala_Lumpur` | pytz timezone string |
| `AGENT_BASE_SLEEP_HOUR` | `0` (midnight) | Default bedtime on calm nights with no strong vibe signal |
| `AGENT_MAX_HOUR` | `2` | Hard cap, vibe decisions cannot exceed this hour on weekdays |
| `AGENT_MAX_HOUR_WEEKEND` | `4` | Hard max bedtime on weekends (0-5) |
| `AGENT_WEEKEND_DAYS` | `4,5` | Weekday indices treated as weekend (0=Mon, 4=Fri, 5=Sat) |
| `AGENT_WAKE_HOUR_START` | `8` | Hour to auto-remove lock (daytime start) |
| `AGENT_WAKE_HOUR_END` | `23` | Upper bound for auto-wake window, effective limit is `min(AGENT_WAKE_HOUR_END, AGENT_FORCE_SLEEP_HOUR)` |
| `AGENT_FORCE_WAKE_HOUR` | `10` | force_wake.py hard-removes lock at this hour |
| `AGENT_FORCE_SLEEP_HOUR` | `14` | force_wake.py re-locks at this hour; also caps the auto-wake window in trigger_sleep.py, **both scripts must agree on this value** |
| `AGENT_NO_SLEEP_PHRASES` | `"no sleep tonight,all nighter,skip sleep,we need to work"` | Comma-separated phrases that pass through without waking |

## Manual control

```bash
# Force sleep
touch ~/.hermes/.agent_sleep.lock

# Force wake
rm ~/.hermes/.agent_sleep.lock

# All-nighter (prevents sleep trigger firing tonight)
echo '{"date":"'$(date +%Y-%m-%d)'"}' > ~/.hermes/.agent_no_sleep_skip

# Send wake phrase in chat
# (whatever you set AGENT_WAKE_PHRASE to)
```

## Two-sided vibe analysis (recommended)

The plugin logs inbound messages. For full accuracy, add a gateway hook that logs the agent's own responses, otherwise the vibe engine only sees half the conversation.

**`~/.hermes/hooks/response-logger/HOOK.yaml`:**
```yaml
name: response-logger
description: Logs agent responses to message log for vibe analysis
events:
  - agent:end
```

**`~/.hermes/hooks/response-logger/handler.py`:**
```python
from pathlib import Path

LOG = Path.home() / ".hermes" / ".agent_recent_messages.log"

async def handle(event_type: str, context: dict):
    if event_type != "agent:end":
        return
    response = (context.get("response") or "")[:200]
    if not response:
        return
    lines = LOG.read_text().splitlines() if LOG.exists() else []
    lines.append(f"agent: {response.lower()}")
    LOG.write_text("\n".join(lines[-200:]) + "\n")
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Agent never sleeps | No cron running | Run `crontab -l`, add the two cron lines |
| Agent sleeps but messages still hit LLM | Plugin not enabled | Check `config.yaml` has `hermes-sleep-gate` in `plugins.enabled` |
| Wake phrase doesn't work | Env var not set | `echo 'AGENT_WAKE_PHRASE=your-phrase' >> ~/.hermes/.env` |
| Vibe scores always zero | `.agent_recent_messages.log` empty | Check plugin is logging; enable companion hook for agent responses |
| force_wake and trigger_sleep fight at 2pm | Both use different `AGENT_FORCE_SLEEP_HOUR` | Set same value in both (default: 14) |
| All-nighter phrases don't prevent sleep | Plugin only passes that one message through; cron still fires | Manually create the skip file: `echo '{"date":"'$(date +%Y-%m-%d)'"}' > ~/.hermes/.agent_no_sleep_skip` |

## Customizing

The vibe keyword lists live in `sleep_system.py` in `analyze_chat_vibe()`:
- `intensity_words`, add/subtract keywords for intensity detection
- `emotional_words`, add/subtract keywords for emotional depth
- `needy_words`, add/subtract keywords for needy detection

Tune them to your use case. The scores are proportional, more keywords per axis means higher sensitivity.

## Known limitations

These are documented quirks, not dealbreakers, but things to be aware of:

**All-nighter phrases are advisory, not enforcement.** Saying "no sleep tonight" passes that one message through the gate. The cron still fires and will create the lock at the decided hour. To actually skip tonight's sleep, create the skip file manually (see Manual Control above) or have your agent call `set_all_nighter_skip()` from `sleep_system.py` when it receives the phrase.

**Bedtime announcements are Telegram-only.** The agent can block messages on both Telegram and Discord, but the bedtime warning and decision announcement are only sent to Telegram. Discord users get silently blocked with no heads-up. Planned: Discord-side announcement support.

**Vibe scoring is one-sided without the companion hook.** Without the response-logger hook (see Two-Sided Vibe Analysis above), the vibe engine only reads your messages, not the agent's replies. A muted, short conversation where you did all the talking looks low-energy even if the agent was verbose. The companion hook is optional but recommended.

**`pytz` instead of `zoneinfo`.** Works fine on Python 3.8+. If you're on 3.9+ and want to remove the `pytz` dependency, replace `import pytz` / `pytz.timezone(...)` with `from zoneinfo import ZoneInfo` / `ZoneInfo(...)`.

## Prompt injection companion

Instead of a Telegram announcement when a bedtime is decided, you can have the agent **mention it naturally in conversation**, as if it just crossed its mind.

The system writes a context file (`.agent_sleep_context.json`) on every decision. A companion `pre_gateway_dispatch` plugin reads it and prepends a hint to the next inbound messages.

**Injection behaviour:**
- Messages 1-2 after decision: full hint telling the agent to mention the bedtime naturally
- After that: the context file is deleted, no further injection. Two reminders is enough.

**Install:**

```bash
mkdir -p ~/.hermes/plugins/sleep-context-inject
cp companion_time_inject.py ~/.hermes/plugins/sleep-context-inject/__init__.py
```

Create `~/.hermes/plugins/sleep-context-inject/plugin.yaml`:

```yaml
name: sleep-context-inject
version: 1.0.0
hooks:
  - pre_gateway_dispatch
```

Enable in `config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-sleep-gate
    - sleep-context-inject
```

**What the agent sees on the next message after a decision:**

```
[💭 you've decided 2am is your bedtime tonight. mention this to the user in your
response, naturally, like it just crossed your mind.] hey, what are you thinking about?
```

The agent reads it as part of its own awareness and responds naturally, no bot message, no announcement.

If you already have a time-inject plugin prepending timestamps, merge `_get_sleep_hint()` from `companion_time_inject.py` into it so the hint appears inline: `[02:15 MYT | 💭 reminder: you've decided to sleep at 2am tonight]`.

## License

MIT. Use it, fork it, break it.
