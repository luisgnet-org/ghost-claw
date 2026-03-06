#!/bin/bash
# io-bridge.sh — PreToolUse hook
#
# Fires on every tool call. Handles:
# 1. Inbox message injection (heartbeats, triggers, timeout)
# 2. Bash command permission gating (allowlist-based)
# 3. Audit logging to session JSONL
#
# Configuration via environment variables:
#   GHOST_AGENT_DIR    — root agent directory (parent of workspace/)
#   GHOST_RUN_DIR      — runtime directory for lockfile + audit
#   GHOST_AGENT_NAME   — agent name for lockfile path (default: claw)

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# --- Resolve paths ---
# Workspace is wherever this hook lives (../../ from .claude/hooks/)
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "$HOOK_DIR/../.." && pwd)"
INBOX="$WORKSPACE/inbox"

AGENT_NAME="${GHOST_AGENT_NAME:-claw}"
AGENT_DIR="${GHOST_AGENT_DIR:-$(cd "$WORKSPACE/.." && pwd)}"
RUN_DIR="${GHOST_RUN_DIR:-$HOME/ghost/ghost_run_dir/workflows/$AGENT_NAME}"
LOCKFILE="$RUN_DIR/.claude.pid"
SESSIONS_DIR="${GHOST_SESSIONS_DIR:-$AGENT_DIR/sessions}"

CONTEXT=""

# --- Detect if we're the primary agent (not a subagent) ---
IS_PRIMARY=false
LOCKFILE_PID=$(cat "$LOCKFILE" 2>/dev/null | tr -d '[:space:]')
if [ -n "$LOCKFILE_PID" ] && [ "$PPID" = "$LOCKFILE_PID" ]; then
    IS_PRIMARY=true
fi

# --- Derive session JSONL path ---
SESSION_JSONL=$(find "$SESSIONS_DIR" -name "session_*.jsonl" -type f -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -1)

# --- Helper: append audit entry to session .jsonl ---
audit_log() {
    [ -n "$SESSION_JSONL" ] && [ -f "$SESSION_JSONL" ] || return 0
    local signal="$1"
    local source_file="$2"
    local payload="$3"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")
    jq -n -c \
        --arg signal "$signal" \
        --arg source "$source_file" \
        --arg ts "$ts" \
        --arg tool "$TOOL_NAME" \
        --argjson payload "$payload" \
        '{
            type: "hook_inject",
            hook: "io-bridge",
            signal: $signal,
            timestamp: $ts,
            source_file: $source,
            tool_context: $tool,
            content: $payload
        }' >> "$SESSION_JSONL"
}

# --- 1. Inject heartbeats, triggers, timeout (primary agent only) ---
if [ "$IS_PRIMARY" = "true" ] && [ -d "$INBOX" ]; then

    # --- 1a. Heartbeat files (heartbeat_*.json) ---
    for f in "$INBOX"/heartbeat_*.json; do
        [ -f "$f" ] || continue
        HB_CONTENT=$(cat "$f")
        CONTEXT="${CONTEXT}HEARTBEAT: ${HB_CONTENT}\n\n"
        audit_log "heartbeat" "$(basename "$f")" "$HB_CONTENT"
        mv "$f" "${f}.read"
    done

    # --- 1b. Trigger files (trigger_*.json) ---
    for f in "$INBOX"/trigger_*.json; do
        [ -f "$f" ] || continue
        TR_CONTENT=$(cat "$f")
        CONTEXT="${CONTEXT}TRIGGER: ${TR_CONTENT}\n\n"
        audit_log "trigger" "$(basename "$f")" "$TR_CONTENT"
        mv "$f" "${f}.read"
    done

    # --- 1c. Timeout warning ---
    WRAPUP="$WORKSPACE/WRAP_UP.md"
    if [ -f "$WRAPUP" ]; then
        WU_CONTENT=$(cat "$WRAPUP")
        CONTEXT="${CONTEXT}SYSTEM: ${WU_CONTENT}\n\n"
        audit_log "wrapup" "WRAP_UP.md" "$(jq -n --arg t "$WU_CONTENT" '{text: $t}')"
        mv "$WRAPUP" "${WRAPUP%.md}_ACK.md"
    fi
fi

# --- 2. Permission gating ---
DECISION="allow"
DENY_MSG=""

case "$TOOL_NAME" in
    # wait_for_message: primary agent only — subagents must not consume inbox
    mcp__telegram__wait_for_message)
        if [ "$IS_PRIMARY" = "false" ]; then
            DECISION="deny"
            DENY_MSG="BLOCKED: wait_for_message is only available to the primary agent."
        fi
        ;;

    # Auto-allow: read-only tools, file tools, web tools, MCP tools, subagents
    Read|Glob|Grep|WebSearch|WebFetch|Write|Edit|NotebookEdit|TodoRead|TodoWrite|Task|Agent|TaskOutput|TaskStop|AskUserQuestion|Skill|ToolSearch|EnterWorktree|mcp__*)
        ;;

    # ALLOW: entering plan mode
    EnterPlanMode)
        ;;

    # ExitPlanMode: conditional on .plan_approved flag
    ExitPlanMode)
        PLAN_FLAG="$WORKSPACE/.plan_approved"
        if [ -f "$PLAN_FLAG" ]; then
            rm -f "$PLAN_FLAG"
            CONTEXT="${CONTEXT}SYSTEM: Plan approved by operator. Proceeding with implementation.\n\n"
        else
            DECISION="deny"
            DENY_MSG="BLOCKED: Plan requires operator approval. Submit your plan via Telegram for review."
        fi
        ;;

    # Bash: allowlist safe commands, block dangerous patterns
    Bash)
        COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

        # DENY: dangerous patterns
        if echo "$COMMAND" | grep -qiE '(sudo |rm -rf /|chmod 777|chown |/etc/passwd|curl.*\|.*sh|nc -l|ncat |mkfs |dd if=)'; then
            DECISION="deny"
            DENY_MSG="BLOCKED: dangerous system command."
        # DENY: writing to /dev/ (except /dev/null, /dev/stdout, /dev/stderr)
        elif echo "$COMMAND" | grep -qE '>\s*/dev/' && ! echo "$COMMAND" | grep -qE '>\s*/dev/(null|stdout|stderr)'; then
            DECISION="deny"
            DENY_MSG="BLOCKED: dangerous system command."
        # DENY: access to sensitive directories
        elif echo "$COMMAND" | grep -qiE '(~/.ssh|~/.aws|~/.env|~/Library|~/Documents|~/Desktop|icloud|\.credentials)'; then
            DECISION="deny"
            DENY_MSG="BLOCKED: access to sensitive directory."
        # DENY: no pushing to remote
        elif echo "$COMMAND" | grep -qiE '^git\s+(push|remote\s+add)'; then
            DECISION="deny"
            DENY_MSG="BLOCKED: git push requires operator approval."
        # ALLOW: build tools, version control, common shell utilities
        elif echo "$COMMAND" | grep -qiE '^(git |gh |npm |npx |node |python|pip |uv |pytest |make |cargo |ls |mkdir |cp |mv |cat |head |tail |wc |diff |curl https|wget https|tar |unzip |zip |jq |sqlite3 |sff |echo |date |which |env |pwd |touch |chmod |find |grep |rg |sort |sed |awk |tee |xargs |bash |sh -c |realpath |dirname |basename |tr |cut |stat |file |du |df |whoami |id |printenv |test |true |false |\[)'; then
            DECISION="allow"
        # ALLOW: npx/node via full path
        elif echo "$COMMAND" | grep -qiE '(^|/)npx[ $]|(^|/)node[ $]'; then
            DECISION="allow"
        # ALLOW: commands scoped to the workspace or ghost directories
        elif echo "$COMMAND" | grep -qE '(ghost/(projects/|agents/|git/))'; then
            DECISION="allow"
        # UNKNOWN: block by default
        else
            DECISION="deny"
            DENY_MSG="BLOCKED: unrecognized command pattern. Ask the operator to approve."
        fi
        ;;

    # Unknown tools: deny
    *)
        DECISION="deny"
        DENY_MSG="BLOCKED: unknown tool '$TOOL_NAME'."
        ;;
esac

# --- Build response ---
if [ -n "$CONTEXT" ] || [ "$DECISION" != "allow" ]; then
    if [ "$DECISION" = "deny" ]; then
        jq -n \
            --arg ctx "$CONTEXT" \
            --arg deny "$DENY_MSG" \
            '{
                hookSpecificOutput: {
                    hookEventName: "PreToolUse",
                    permissionDecision: "deny",
                    denyMessage: $deny,
                    additionalContext: $ctx
                }
            }'
    else
        jq -n \
            --arg ctx "$CONTEXT" \
            '{
                hookSpecificOutput: {
                    hookEventName: "PreToolUse",
                    permissionDecision: "allow",
                    additionalContext: $ctx
                }
            }'
    fi
else
    exit 0
fi
