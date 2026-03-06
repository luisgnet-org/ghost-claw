#!/bin/bash
# inbox-notify.sh — PostToolUse hook
#
# Fires AFTER every tool call. Injects inbox nudge so the agent
# knows there are pending messages to consume.

INPUT=$(cat)

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "$HOOK_DIR/../.." && pwd)"
INBOX="$WORKSPACE/inbox"

AGENT_NAME="${GHOST_AGENT_NAME:-claw}"
RUN_DIR="${GHOST_RUN_DIR:-$HOME/ghost/ghost_run_dir/workflows/$AGENT_NAME}"
LOCKFILE="$RUN_DIR/.claude.pid"

# --- Primary agent check ---
IS_PRIMARY=false
LOCKFILE_PID=$(cat "$LOCKFILE" 2>/dev/null | tr -d '[:space:]')
if [ -n "$LOCKFILE_PID" ] && [ "$PPID" = "$LOCKFILE_PID" ]; then
    IS_PRIMARY=true
fi

[ "$IS_PRIMARY" = "true" ] || exit 0
[ -d "$INBOX" ] || exit 0

# Count unread messages
MSG_COUNT=0
for f in "$INBOX"/msg_*.json; do
    [ -f "$f" ] || continue
    MSG_COUNT=$((MSG_COUNT + 1))
done

[ "$MSG_COUNT" -gt 0 ] || exit 0

# Emit context nudge
jq -n \
    --argjson count "$MSG_COUNT" \
    '{
        hookSpecificOutput: {
            hookEventName: "PostToolUse",
            additionalContext: ("INBOX: " + ($count | tostring) + " unread message(s) waiting — call wait_for_message to receive them.")
        }
    }'
