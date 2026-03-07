#!/bin/bash
# bin/setup-check.sh — Live setup verification checklist
#
# Polls Telegram API and local state to show which setup steps are complete.
# Updates in-place until all checks pass or user hits Ctrl+C.
#
# Usage:
#   bin/setup-check.sh                    # auto-locates GHOST_HOME
#   bin/setup-check.sh --home ~/myagent   # explicit GHOST_HOME
#   bin/setup-check.sh --once             # print once and exit (non-interactive)

set -euo pipefail

# ── Self-locate ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GHOST_HOME_DEFAULT="$(cd "$PLUGIN_DIR/../.." && pwd)"

GHOST_HOME_ARG=""
ONCE=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --home) GHOST_HOME_ARG="${2/#\~/$HOME}"; shift 2 ;;
        --once) ONCE=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done
GHOST_HOME="${GHOST_HOME_ARG:-$GHOST_HOME_DEFAULT}"
ENV_FILE="$GHOST_HOME/.env"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

pass() { printf "  ${GREEN}✓${NC}  %-42s ${DIM}%s${NC}\n" "$1" "$2"; }
fail() { printf "  ${RED}✗${NC}  %-42s ${DIM}%s${NC}\n" "$1" "$2"; }
wait_() { printf "  ${YELLOW}○${NC}  %-42s ${DIM}%s${NC}\n" "$1" "$2"; }

# ── Load .env ─────────────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
fi
TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TG_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
AGENT_DIR="$GHOST_HOME/agents/claw"
AGENT_HOME="$AGENT_DIR/home"

# ── Individual checks ─────────────────────────────────────────────────────────

# Returns: "ok <bot_username>" or "fail"
check_bot_token() {
    [ -z "$TG_TOKEN" ] && { echo "fail"; return; }
    local resp
    resp=$(curl -sf --max-time 5 "https://api.telegram.org/bot${TG_TOKEN}/getMe" 2>/dev/null || true)
    local name
    name=$(echo "$resp" | python3 -c "
import sys, json
r = json.load(sys.stdin)
if r.get('ok'):
    print('@' + r['result']['username'])
" 2>/dev/null || true)
    [ -n "$name" ] && echo "ok $name" || echo "fail"
}

# Returns: "ok <chat_title>" or "fail" or "none"
check_group() {
    [ -z "$TG_TOKEN" ] || [ -z "$TG_CHAT_ID" ] && { echo "none"; return; }
    local resp
    resp=$(curl -sf --max-time 5 \
        "https://api.telegram.org/bot${TG_TOKEN}/getChat?chat_id=${TG_CHAT_ID}" \
        2>/dev/null || true)
    python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    if r.get('ok'):
        title = r['result'].get('title') or r['result'].get('username') or '?'
        print('ok', title)
    else:
        print('fail')
except:
    print('fail')
" <<< "$resp" 2>/dev/null || echo "fail"
}

# Returns: "ok" or "fail" or "none"
check_topics_enabled() {
    [ -z "$TG_TOKEN" ] || [ -z "$TG_CHAT_ID" ] && { echo "none"; return; }
    local resp
    resp=$(curl -sf --max-time 5 \
        "https://api.telegram.org/bot${TG_TOKEN}/getChat?chat_id=${TG_CHAT_ID}" \
        2>/dev/null || true)
    python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    if r.get('ok') and r['result'].get('is_forum'):
        print('ok')
    else:
        print('fail')
except:
    print('fail')
" <<< "$resp" 2>/dev/null || echo "fail"
}

# Returns: "ok" or "fail" or "none"
check_bot_admin() {
    [ -z "$TG_TOKEN" ] || [ -z "$TG_CHAT_ID" ] && { echo "none"; return; }
    local resp
    resp=$(curl -sf --max-time 5 \
        "https://api.telegram.org/bot${TG_TOKEN}/getChatAdministrators?chat_id=${TG_CHAT_ID}" \
        2>/dev/null || true)
    python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    for m in r.get('result', []):
        if m.get('user', {}).get('is_bot') and m.get('can_manage_topics'):
            print('ok')
            exit()
    print('fail')
except:
    print('fail')
" <<< "$resp" 2>/dev/null || echo "fail"
}

# Returns: "ok" or "fail"
# Claude Code creates several files in $HOME/.claude/ on first login/use.
# We check for any file that the installer does NOT place there.
check_claude_login() {
    # Installer only creates $AGENT_HOME/.claude/ if anything — we don't touch it.
    # Claude creates history.jsonl, statsig/, session-env/, etc. on first run.
    if [ -d "$AGENT_HOME/.claude" ] && [ -n "$(ls -A "$AGENT_HOME/.claude" 2>/dev/null)" ]; then
        echo "ok"
    else
        echo "fail"
    fi
}

# ── Draw the status block ─────────────────────────────────────────────────────
draw() {
    local bot_result="$1"
    local group_result="$2"
    local topics_result="$3"
    local admin_result="$4"
    local claude_result="$5"
    local all_ok=true

    # 1. Bot token
    if [[ "$bot_result" == ok* ]]; then
        pass "Bot token valid" "${bot_result#ok }"
    else
        fail "Bot token valid" "check TELEGRAM_BOT_TOKEN in .env"
        all_ok=false
    fi

    # 2. Telegram group
    if [[ "$group_result" == ok* ]]; then
        pass "Telegram group found" "${group_result#ok }"
    elif [ "$group_result" = "none" ]; then
        wait_ "Telegram group" "create group, add bot, send any message"
        all_ok=false
    else
        fail "Telegram group" "can't reach group — check TELEGRAM_CHAT_ID in .env"
        all_ok=false
    fi

    # 3. Topics enabled
    if [ "$topics_result" = "ok" ]; then
        pass "Topics enabled on group" ""
    elif [ "$topics_result" = "none" ]; then
        wait_ "Topics enabled on group" "waiting for group..."
        all_ok=false
    else
        fail "Topics enabled on group" "group → ··· → Edit → Topics → ON"
        all_ok=false
    fi

    # 4. Bot is Admin with Manage Topics
    if [ "$admin_result" = "ok" ]; then
        pass "Bot is Admin (Manage Topics)" ""
    elif [ "$admin_result" = "none" ]; then
        wait_ "Bot is Admin (Manage Topics)" "waiting for group..."
        all_ok=false
    else
        fail "Bot is Admin (Manage Topics)" "group → Edit → Administrators → add bot"
        all_ok=false
    fi

    # 5. Claude logged in
    if [ "$claude_result" = "ok" ]; then
        pass "Claude Code authenticated" "$AGENT_HOME"
    else
        fail "Claude Code authenticated" "run: HOME=$AGENT_HOME claude"
        all_ok=false
    fi

    # Summary line
    echo ""
    if [ "$all_ok" = true ]; then
        echo -e "  ${GREEN}${BOLD}All checks passed — agent is ready!${NC}"
        echo ""
        echo -e "  ${DIM}Send a message to your bot in Telegram to wake it up.${NC}"
        echo ""
        return 0
    else
        echo -e "  ${DIM}Checking again in 5s... (Ctrl+C to exit)${NC}"
        echo ""
        return 1
    fi
}

# ── Main loop ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD} Ghost Setup Status${NC}  ${DIM}($GHOST_HOME)${NC}"
echo -e " ────────────────────────────────────────────────────────"
echo ""

if [ "$ONCE" = true ]; then
    bot_r=$(check_bot_token)
    group_r=$(check_group)
    topics_r=$(check_topics_enabled)
    admin_r=$(check_bot_admin)
    claude_r=$(check_claude_login)
    draw "$bot_r" "$group_r" "$topics_r" "$admin_r" "$claude_r" || true
    exit 0
fi

# Interactive loop: redraw in-place using cursor save/restore.
# Cache stable results (token, group, topics) so we don't re-check them every second.
bot_r=$(check_bot_token)
group_r=$(check_group)
topics_r=$(check_topics_enabled)

tput sc   # save cursor position once, before first draw
while true; do
    # Only re-poll things that can change while waiting
    [[ "$topics_r" != "ok" ]] && { group_r=$(check_group); topics_r=$(check_topics_enabled); }
    admin_r=$(check_bot_admin)
    claude_r=$(check_claude_login)

    tput rc; tput ed   # restore + clear to end of screen
    if draw "$bot_r" "$group_r" "$topics_r" "$admin_r" "$claude_r"; then
        break
    fi
    sleep 1
done
