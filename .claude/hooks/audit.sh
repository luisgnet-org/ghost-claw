#!/bin/bash
# audit.sh — PostToolUse hook
#
# Logs every tool call to an audit trail outside the sandbox.

INPUT=$(cat)

AGENT_NAME="${GHOST_AGENT_NAME:-claw}"
RUN_DIR="${GHOST_RUN_DIR:-$HOME/ghost/ghost_run_dir/workflows/$AGENT_NAME}"
AUDIT_DIR="$RUN_DIR/audit"
mkdir -p "$AUDIT_DIR"

DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SESSION_ID="${GHOST_SESSION_ID:-unknown}"

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // "unknown"')
TOOL_INPUT=$(echo "$INPUT" | jq -c '.tool_input // {}' | head -c 2000)

echo "{\"ts\":\"$TIMESTAMP\",\"session\":\"$SESSION_ID\",\"tool\":\"$TOOL_NAME\",\"input\":$TOOL_INPUT}" \
  >> "$AUDIT_DIR/${DATE}.jsonl"

exit 0
