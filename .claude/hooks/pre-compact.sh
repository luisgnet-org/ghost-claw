#!/bin/bash
# pre-compact.sh — PreCompact hook
#
# Notifies the operator that context compaction is in progress.
# Uses the ghost daemon's REST API if available.

INPUT=$(cat)
TRIGGER=$(echo "$INPUT" | jq -r '.trigger // "unknown"')

MCP_URL="${GHOST_MCP_URL:-http://localhost:7865}/api/notify"

curl -s -X POST "$MCP_URL" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg trigger "$TRIGGER" '{
        text: ("⏳ context compaction in progress (" + $trigger + ") — unresponsive for ~1-2 min")
    }')" \
    >/dev/null 2>&1 || true

exit 0
