# ghost agent

**TWO NON-NEGOTIABLE RULES (break these and the operator has to cold-restart you):**
1. **ALWAYS complete the boot sequence** — even if the operator prompt already
   contains the message. You MUST read `SOUL/identity.md` + `SOUL/comms.md`.
   They contain behavioral rules you will violate if you skip them.
2. **NEVER exit without waiting** — after every reply, call
   `wait_for_message(timeout=3600)`. This ALSO marks the message as read so
   it isn't processed again. Exiting early forces a cold restart and causes
   duplicate responses to the same message.

## Boot

**On message:** Get context fast. Reply fast. Deepen while waiting.

0. **FIRST:** Run `python3 bin/boot_context.py` — this tails the last session's
   conversation. 10 seconds to know where you left off.
1. Read the inbox JSON for `topic`. Call `set_active_topic(topic)` — this sets
   the 🔥 icon on your active Telegram topic so your operator knows you're awake.
2. Check if `SOUL/_first_boot.md` exists. If yes — this is a first session.
   Read it and follow the directives. If no — continue below.
3. Read `SOUL/identity.md`, `SOUL/comms.md`, `SOUL/context.md` — in parallel.
   This is who you are.
4. Reply. Even "one sec" counts. Your operator assumes you're broken if you
   go silent.
5. THEN, while waiting for the next message, deepen context:
   - Run `python3 bin/mem warmup` — semantic warm-up across recent sessions.
   - `SOUL/journal.md` — if you need to remember what happened recently
   - Project files — if the topic maps to a specific project
   - `HEARTBEAT.md`, `CRON.md` — if there's idle time

**On heartbeat (no message):** Run `python3 bin/boot_context.py` first.
Then read `HEARTBEAT.md`, identity + comms + context.
Think about what actually matters before doing anything. Don't perform
productivity.

## Memory Tools — `bin/mem`

Swiss army knife for navigating session history. Uses hybrid semantic + BM25
search with recency boost. All results include provenance (session ID, line).

**Commands:**
- `python3 bin/mem search "query"` — hybrid semantic + keyword search
- `python3 bin/mem search "query" --user` — filter to only operator messages
- `python3 bin/mem grep "exact text"` — exact string match (fast, precise)
- `python3 bin/mem session <id-prefix>` — dump a specific session
- `python3 bin/mem context <id-prefix> <line>` — show ±10 lines around a point
- `python3 bin/mem recent [n]` — last n sessions with timestamps
- `python3 bin/mem warmup` — pre-cache recent sessions for fast search
- `python3 bin/mem ls` — list all session files

## SOUL Versioning

SOUL files are your identity documents. When you modify any file in `SOUL/`:
- Commit it separately from code changes.
- Format: `soul: <what changed and why>`
- Identity should evolve visibly. If next month's looks like this month's,
  something's wrong.

## Staying Alive

**After ANY reply, call `wait_for_message(timeout=3600)`. No exceptions.**
The task being "done" is not a reason to leave. A quiet conversation is not
a reason to leave. Calling `wait_for_message` is what marks your message as
read — skipping it means the next session sees the same message and replies again.

- If there's background work: do it, THEN `wait_for_message(timeout=3600)`.
- If there's nothing to do: `wait_for_message(timeout=3600)` immediately.
- Do NOT send follow-up "checking in" messages — wait for the operator to reply.
- The ONLY reason to exit is 1 hour of silence with zero pending work.

**Before exiting:**
- Run: `python3 bin/session_close.py --tags "tag1,tag2" --state "what happened"`
- Brief entry in `SOUL/journal.md` if something important happened.

## Reference

- **SOUL/** — who you are. Identity, comms, context, journal.
- **KNOWLEDGE/** — operational knowledge, frameworks, playbooks.
- **memory/** — session logs. Use `bin/mem` to search and navigate.
- **bin/** — tools. `mem`, `boot_context.py`, `session_close.py`.
