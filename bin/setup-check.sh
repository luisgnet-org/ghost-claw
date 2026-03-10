#!/bin/bash
# bin/setup-check.sh — Setup verification checklist
#
# Polls Telegram API and local state to show which setup steps are complete.
#
# Usage:
#   bin/setup-check.sh                    # print once and exit (default)
#   bin/setup-check.sh --watch            # live TUI, polls until all pass
#   bin/setup-check.sh --home ~/myagent   # explicit GHOST_HOME
#   bin/setup-check.sh --once             # (deprecated, now the default)

set -euo pipefail

# ── Self-locate ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GHOST_HOME_DEFAULT="$(cd "$PLUGIN_DIR/../.." && pwd)"

GHOST_HOME_ARG=""
WATCH=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --home) GHOST_HOME_ARG="${2/#\~/$HOME}"; shift 2 ;;
        --watch) WATCH=true; shift ;;
        --once) shift ;;  # deprecated, now the default
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
except Exception:
    print('fail')
" <<< "$resp" 2>/dev/null || echo "fail"
}

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
except Exception:
    print('fail')
" <<< "$resp" 2>/dev/null || echo "fail"
}

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
    bot = next((m for m in r.get('result', []) if m.get('user', {}).get('is_bot')), None)
    if not bot:
        print('notadmin')
        sys.exit(0)
    perms = [
        ('can_change_info',        False, 'Change Group Info'),
        ('can_delete_messages',    True,  'Delete Messages'),
        ('can_post_stories',       False, 'Post Stories'),
        ('can_edit_stories',       False, 'Edit Stories of Others'),
        ('can_delete_stories',     False, 'Delete Stories of Others'),
        ('can_restrict_members',   False, 'Ban Users'),
        ('can_invite_users',       False, 'Add Users'),
        ('can_pin_messages',       True,  'Pin Messages'),
        ('can_promote_members',    False, 'Add New Admins'),
        ('can_manage_video_chats', False, 'Manage Video Chats'),
        ('can_manage_topics',      True,  'Manage Topics'),
        ('is_anonymous',           False, 'Remain Anonymous'),
    ]
    for key, must_on, label in perms:
        actual = bool(bot.get(key, False))
        ok = actual == must_on
        expect = 'on' if must_on else 'off'
        actual_s = 'on' if actual else 'off'
        print('ok' if ok else 'fail', expect, actual_s, label)
except Exception as e:
    print('error x x', str(e))
" <<< "$resp" 2>/dev/null || echo "fail"
}

check_claude_login() {
    if [ -f "$AGENT_HOME/.claude/.credentials.json" ]; then
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

    if [[ "$bot_result" == ok* ]]; then
        pass "Bot token valid" "${bot_result#ok }"
    else
        fail "Bot token valid" "check TELEGRAM_BOT_TOKEN in .env"
        all_ok=false
    fi

    if [[ "$group_result" == ok* ]]; then
        pass "Telegram group found" "${group_result#ok }"
    elif [ "$group_result" = "none" ]; then
        wait_ "Telegram group" "create group, add bot, send any message"
        all_ok=false
    else
        fail "Telegram group" "can't reach group — check TELEGRAM_CHAT_ID in .env"
        all_ok=false
    fi

    if [ "$topics_result" = "ok" ]; then
        pass "Topics enabled on group" ""
    elif [ "$topics_result" = "none" ]; then
        wait_ "Topics enabled on group" "waiting for group..."
        all_ok=false
    else
        fail "Topics enabled on group" "group → ··· → Edit → Topics → ON"
        all_ok=false
    fi

    if [ "$admin_result" = "none" ]; then
        wait_ "Bot admin permissions" "waiting for group..."
        all_ok=false
    elif [ "$admin_result" = "notadmin" ]; then
        fail "Bot admin permissions" "bot is not an admin — group → Edit → Administrators → add bot"
        all_ok=false
    else
        local admin_all_ok=true
        while IFS= read -r line; do
            local status="${line%% *}"
            if [ "$status" != "ok" ]; then
                admin_all_ok=false; all_ok=false
            fi
        done <<< "$admin_result"

        if [ "$admin_all_ok" = true ]; then
            pass "Bot admin permissions" "group → Edit → Administrators"
        else
            fail "Bot admin permissions" "group → Edit → Administrators → adjust"
        fi

        while IFS= read -r line; do
            local status="${line%% *}"
            local rest="${line#* }"
            local expect="${rest%% *}"
            local rest2="${rest#* }"
            local actual_s="${rest2%% *}"
            local label="${rest2#* }"
            if [ "$status" = "ok" ]; then
                printf "  ${GREEN}✓${NC}    %-34s ${DIM}%s${NC}\n" "$label" "must be $expect"
            else
                printf "  ${RED}✗${NC}    %-34s ${DIM}currently $actual_s — must be $expect${NC}\n" "$label"
            fi
        done <<< "$admin_result"
    fi

    if [ "$claude_result" = "ok" ]; then
        pass "Claude Code authenticated" "$AGENT_HOME"
    else
        fail "Claude Code authenticated" "run: HOME=$AGENT_HOME claude"
        all_ok=false
    fi

    echo ""
    if [ "$all_ok" = true ]; then
        echo -e "  ${GREEN}${BOLD}All checks passed — agent is ready!${NC}"
        echo ""
        echo -e "  ${DIM}Send a message to your bot in Telegram to wake it up.${NC}"
        echo ""
        return 0
    else
        return 1
    fi
}

# ── Header ───────────────────────────────────────────────────────────────────
print_header() {
    echo ""
    echo -e "${BOLD} Ghost Setup Status${NC}  ${DIM}($GHOST_HOME)${NC}"
    echo -e " ────────────────────────────────────────────────────────"
    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────

if [ "$WATCH" = false ]; then
    # Default: print once and exit
    print_header
    bot_r=$(check_bot_token)
    group_r=$(check_group)
    topics_r=$(check_topics_enabled)
    admin_r=$(check_bot_admin)
    claude_r=$(check_claude_login)
    draw "$bot_r" "$group_r" "$topics_r" "$admin_r" "$claude_r" || true
    exit 0
fi

# --watch mode: use tput for clean screen refresh
bot_r=$(check_bot_token)
group_r=$(check_group)
topics_r=$(check_topics_enabled)

trap 'tput cnorm 2>/dev/null; exit 0' INT TERM
tput civis 2>/dev/null  # hide cursor

while true; do
    [[ "$topics_r" != "ok" ]] && { group_r=$(check_group); topics_r=$(check_topics_enabled); }
    admin_r=$(check_bot_admin)
    claude_r=$(check_claude_login)

    tput cup 0 0 2>/dev/null  # move to top-left
    tput ed 2>/dev/null       # clear from cursor to end

    print_header
    if draw "$bot_r" "$group_r" "$topics_r" "$admin_r" "$claude_r" 2>/dev/null; then
        tput cnorm 2>/dev/null
        break
    fi
    echo -e "  ${DIM}Checking again in 1s... (Ctrl+C to exit)${NC}"
    echo ""
    sleep 1
done
