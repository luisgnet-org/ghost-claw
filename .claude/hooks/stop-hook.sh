#!/bin/bash
# stop-hook.sh — Stop hook
#
# Intercepts session exit. On first stop attempt, blocks and prompts
# the agent to call wait_for_message instead. If the agent stops again
# within 3 minutes (same session), the gate opens and exit proceeds.

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "$HOOK_DIR/../.." && pwd)"

STATE_DIR="$WORKSPACE/.claude/state"
GATE_FILE="$STATE_DIR/stop_gate"
INBOX="$WORKSPACE/inbox"
LOG_FILE="$STATE_DIR/stop_hook.log"

mkdir -p "$STATE_DIR"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) stop-hook fired PPID=$PPID inbox=$(ls "$INBOX"/msg_*.json 2>/dev/null | wc -l | tr -d ' ') gate=$(cat "$GATE_FILE" 2>/dev/null || echo none)" >> "$LOG_FILE"

# --- Inbox check (always) ---
INBOX_MSG=$(ls "$INBOX"/msg_*.json 2>/dev/null | head -1)
if [ -n "$INBOX_MSG" ]; then
    rm -f "$GATE_FILE"
    jq -n '{
        hookSpecificOutput: {
            hookEventName: "Stop",
            stopDecision: "block",
            reason: "You have unprocessed inbox messages. Check the inbox and respond before exiting."
        }
    }'
    exit 0
fi

# --- Session-scoped gate check ---
NOW=$(date +%s)

if [ -f "$GATE_FILE" ]; then
    read -r LAST GATE_PID < "$GATE_FILE"
    AGE=$((NOW - LAST))
    if [ "$GATE_PID" = "$PPID" ] && [ "$AGE" -le 180 ]; then
        rm -f "$GATE_FILE"
        exit 0
    fi
fi

# First stop — write gate and block
echo "$NOW $PPID" > "$GATE_FILE"

jq -n '{
    hookSpecificOutput: {
        hookEventName: "Stop",
        stopDecision: "block",
        reason: "You were about to end the session. Your operator prefers you stay available — call wait_for_message(timeout=3600) to keep listening. If you genuinely need to exit, stop again."
    }
}'
