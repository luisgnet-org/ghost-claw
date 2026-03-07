#!/bin/bash
# install.sh — Ghost + Claw one-command installer
#
# Usage:
#   ./install.sh --home DIR [--instance-id ID] [--ghost-repo URL]
#                [--no-launchd] [--no-start]
#
# Required:
#   --home DIR     Where to install (e.g. ~/ghost or ~/myagent). Must not exist
#                  or be empty — will not clobber an existing install.
#
# Defaults:
#   --instance-id  derived from basename of --home
#   --ghost-repo   https://github.com/luisgnet-org/ghost.git
#
# What this does:
#   1. Creates directory structure under GHOST_HOME
#   2. Clones the ghost daemon repo
#   3. Creates a Python venv and installs dependencies
#   4. Guides you through .env setup (with Telegram chat ID auto-detection)
#   5. Sets up the claw agent workspace
#   6. Generates namespaced launchd services (no conflicts with other installs)
#   7. Starts everything
#
# To uninstall: ./uninstall.sh --home <same DIR>

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
GHOST_HOME_ARG=""
GHOST_REPO="https://github.com/luisgnet-org/ghost.git"
INSTANCE_ID=""
AGENT_NAME="claw"
SKIP_LAUNCHD=false
NO_START=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --home)        GHOST_HOME_ARG="$2"; shift 2 ;;
        --instance-id) INSTANCE_ID="$2";    shift 2 ;;
        --ghost-repo)  GHOST_REPO="$2";     shift 2 ;;
        --no-launchd)  SKIP_LAUNCHD=true;   shift ;;
        --no-start)    NO_START=true;       shift ;;
        --help|-h)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# --home is required
if [ -z "$GHOST_HOME_ARG" ]; then
    echo ""
    echo "Error: --home DIR is required."
    echo ""
    echo "Usage: ./install.sh --home ~/myagent"
    echo ""
    echo "Choose a directory that doesn't exist yet (e.g. ~/ghost, ~/myagent)."
    echo "It must not conflict with an existing ghost install."
    exit 1
fi

# Expand ~ manually (works even when not in interactive shell)
GHOST_HOME="${GHOST_HOME_ARG/#\~/$HOME}"

# Require absolute path
if [[ "$GHOST_HOME" != /* ]]; then
    echo ""
    echo "Error: --home must be an absolute path (got: $GHOST_HOME)"
    echo "       Use ~/myagent or /Users/you/myagent, not a relative path."
    echo ""
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$SCRIPT_DIR"

# Reject if GHOST_HOME is inside the plugin directory (relative path mistake)
if [[ "$GHOST_HOME" == "$PLUGIN_DIR"* ]]; then
    echo ""
    echo "Error: --home cannot be inside the ghost-claw repo ($PLUGIN_DIR)."
    echo "       Choose a separate directory, e.g. ~/myagent"
    echo ""
    exit 1
fi

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info() { echo -e "${BLUE}→${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*" >&2; }
hr()   { echo "──────────────────────────────────────────────────────────"; }

# ── Derive instance ID ────────────────────────────────────────────────────────
if [ -z "$INSTANCE_ID" ]; then
    INSTANCE_ID="$(basename "$GHOST_HOME")"
fi
LABEL_PREFIX="com.ghost.$INSTANCE_ID"
LAUNCHD_DIR="$HOME/Library/LaunchAgents"

# ── Check for launchd conflicts ───────────────────────────────────────────────
check_conflicts() {
    local conflicts=()
    for svc in daemon mcp-proxy claw-session; do
        [ -f "$LAUNCHD_DIR/$LABEL_PREFIX.$svc.plist" ] && conflicts+=("$LABEL_PREFIX.$svc")
    done
    if [ ${#conflicts[@]} -gt 0 ]; then
        warn "Instance '$INSTANCE_ID' is already installed — monitoring existing installation instead."
        echo ""
        VENV="$GHOST_HOME/venv"
        if [ -x "$VENV/bin/python3" ] && [ -f "$PLUGIN_DIR/bin/status.py" ]; then
            echo -e "${DIM} (exit process at any time)${NC}"
            echo ""
            exec "$VENV/bin/python3" "$PLUGIN_DIR/bin/status.py" --home "$GHOST_HOME"
        else
            err "Could not find status monitor. To reinstall:"
            err "  • Use a different name:  ./install.sh --home $GHOST_HOME --instance-id <unique-name>"
            err "  • Uninstall first:       ./uninstall.sh --home $GHOST_HOME"
            exit 1
        fi
    fi
}

# ── Find a free port pair (proxy + backend) ───────────────────────────────────
find_free_ports() {
    local port="${1:-7865}"
    while [ "$port" -lt 7999 ]; do
        if ! lsof -i ":$port" >/dev/null 2>&1 && \
           ! lsof -i ":$((port+1))" >/dev/null 2>&1; then
            echo "$port $((port+1))"
            return
        fi
        port=$((port+2))
    done
    err "Could not find a free port pair in 7865-7999"
    exit 1
}

# ── Telegram: auto-detect chat ID ────────────────────────────────────────────
tg_get_chat_id() {
    # All UI output goes to /dev/tty so it doesn't pollute the $(…) capture.
    # Only the bare chat ID is printed to stdout at the very end.
    local token="$1"
    {
        echo ""
        hr
        echo -e "${BOLD} Telegram Chat ID Setup${NC}"
        hr
        echo ""
        echo " Ghost needs a Telegram group chat to send messages to."
        echo ""
        echo " Steps:"
        echo "   1. In Telegram, create a new Group (not a Channel)"
        echo "   2. Add your bot to the group"
        echo "   3. Send ANY message in the group (e.g. 'hello')"
        echo ""
        echo " Waiting for your message... (up to 10 minutes)"
        echo " Press Ctrl+C to enter the chat ID manually instead."
        echo ""
    } > /dev/tty

    # Get current update offset so we only catch fresh messages
    local offset=0
    local offset_resp
    offset_resp=$(curl -sf "https://api.telegram.org/bot${token}/getUpdates?limit=1" 2>/dev/null || true)
    if [ -n "$offset_resp" ]; then
        offset=$(echo "$offset_resp" | python3 -c "
import sys, json
r = json.load(sys.stdin)
results = r.get('result', [])
print(results[-1]['update_id'] + 1 if results else 0)
" 2>/dev/null || echo "0")
    fi

    local chat_id=""
    local chat_title=""
    local attempts=0

    while [ -z "$chat_id" ] && [ "$attempts" -lt 60 ]; do
        local resp
        resp=$(curl -sf \
            "https://api.telegram.org/bot${token}/getUpdates?offset=${offset}&timeout=10" \
            2>/dev/null || true)

        if [ -n "$resp" ]; then
            local result
            result=$(echo "$resp" | python3 -c "
import sys, json
r = json.load(sys.stdin)
for u in r.get('result', []):
    msg = u.get('message') or u.get('channel_post') or {}
    chat = msg.get('chat', {})
    cid = chat.get('id')
    title = (chat.get('title') or chat.get('username') or chat.get('first_name') or '')
    if cid:
        print(f'{cid}|{title}')
        break
" 2>/dev/null || true)

            if [ -n "$result" ]; then
                chat_id="${result%%|*}"
                chat_title="${result##*|}"
            fi
        fi
        attempts=$((attempts+1))
    done

    if [ -n "$chat_id" ]; then
        {
            echo ""
            ok "Found chat: ${chat_title:-unknown} (ID: $chat_id)"
        } > /dev/tty
        echo "$chat_id"   # only this reaches the $() caller
    else
        {
            echo ""
            warn "Timed out — no message received."
            printf " Enter Telegram chat ID manually (negative number for groups): "
        } > /dev/tty
        local manual_id
        read -r manual_id < /dev/tty
        echo "$manual_id"
    fi
}

# ── Render plist template ─────────────────────────────────────────────────────
render_plist() {
    local tmpl="$1" dest="$2"
    sed \
        -e "s|__LABEL_PREFIX__|$LABEL_PREFIX|g" \
        -e "s|__INSTANCE__|$INSTANCE_ID|g" \
        -e "s|__GHOST_HOME__|$GHOST_HOME|g" \
        -e "s|__VENV__|$VENV|g" \
        -e "s|__AGENT_DIR__|$AGENT_DIR|g" \
        -e "s|__AGENT_NAME__|$AGENT_NAME|g" \
        -e "s|__MCP_PROXY_PORT__|$MCP_PROXY_PORT|g" \
        -e "s|__MCP_BACKEND_PORT__|$MCP_BACKEND_PORT|g" \
        "$tmpl" > "$dest"
}

# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}╔═══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║       ghost + claw  installer         ║${NC}"
echo -e "${BOLD}╚═══════════════════════════════════════╝${NC}"
echo ""
info "Ghost home:   $GHOST_HOME"
info "Instance ID:  $INSTANCE_ID"
info "Label prefix: $LABEL_PREFIX"
echo ""

# Conflict check before doing anything
[ "$SKIP_LAUNCHD" = false ] && check_conflicts

# ── 1. Directory structure ────────────────────────────────────────────────────
info "Creating directories..."
AGENT_DIR="$GHOST_HOME/agents/$AGENT_NAME"
mkdir -p "$GHOST_HOME"/{git,ghost_run_dir}
mkdir -p "$GHOST_HOME/ghost_run_dir"/{workflows,telegram}
mkdir -p "$AGENT_DIR"/{workspace,sessions,home}
mkdir -p "$AGENT_DIR/workspace/inbox"
mkdir -p "$AGENT_DIR/workspace/memory/log"
mkdir -p "$GHOST_HOME/ghost_run_dir/workflows/$AGENT_NAME/audit"
ok "Directories ready"

# ── 2. Clone ghost daemon ─────────────────────────────────────────────────────
GHOST_GIT="$GHOST_HOME/git/ghost"
if [ -d "$GHOST_GIT/.git" ]; then
    ok "Ghost daemon already cloned at $GHOST_GIT"
else
    info "Cloning ghost daemon..."
    git clone "$GHOST_REPO" "$GHOST_GIT"
    ok "Ghost daemon cloned"
fi

# ── 3. Link ghost-claw ────────────────────────────────────────────────────────
CLAW_GIT="$GHOST_HOME/git/ghost_claw"
if [ -d "$CLAW_GIT/.git" ]; then
    ok "Ghost-claw already at $CLAW_GIT"
else
    info "Linking ghost-claw plugin..."
    mkdir -p "$(dirname "$CLAW_GIT")"
    ln -sf "$PLUGIN_DIR" "$CLAW_GIT"
    ok "Ghost-claw linked → $CLAW_GIT"
fi

# ── 4. Python venv ────────────────────────────────────────────────────────────
VENV="$GHOST_HOME/venv"
if [ -d "$VENV" ]; then
    ok "Venv already exists"
else
    info "Creating Python venv..."
    python3 -m venv "$VENV"
    ok "Venv created"
fi
info "Installing Python dependencies..."
"$VENV/bin/pip" install -q --upgrade pip uv
VIRTUAL_ENV="$VENV" "$VENV/bin/uv" pip install -q -r "$GHOST_GIT/requirements.txt"
ok "Dependencies installed (via uv)"

if "$VENV/bin/python3" -c "import sff" 2>/dev/null; then
    ok "sff (semantic search) available"
else
    warn "sff not installed — bin/mem will use keyword search only"
    warn "  Install later: $VENV/bin/uv pip install sff"
fi

# ── 5. .env configuration ─────────────────────────────────────────────────────
ENV_FILE="$GHOST_HOME/.env"

# Load existing .env if present
TG_TOKEN=""
TG_CHAT_ID=""
MCP_PROXY_PORT=""
MCP_BACKEND_PORT=""
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
    TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
    TG_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
    MCP_PROXY_PORT="${MCP_PROXY_PORT:-}"
    MCP_BACKEND_PORT="${MCP_BACKEND_PORT:-}"
fi

ENV_UPDATED=false

# Prompt for missing values
if [ -z "$TG_TOKEN" ] || [ -z "$TG_CHAT_ID" ]; then
    echo ""
    hr
    echo -e "${BOLD} Environment Setup${NC}"
    hr
    echo ""
fi

# Telegram bot token
if [ -n "$TG_TOKEN" ]; then
    ok "Telegram bot token already set — skipping"
else
    echo " You'll need:"
    echo "   • Telegram bot token  — create one at https://t.me/BotFather"
    echo ""
    printf " Telegram bot token: "
    read -r TG_TOKEN
    ENV_UPDATED=true

    # Validate token
    BOT_RESP=$(curl -sf "https://api.telegram.org/bot${TG_TOKEN}/getMe" 2>/dev/null || true)
    if echo "$BOT_RESP" | python3 -c "import sys,json; r=json.load(sys.stdin); exit(0 if r.get('ok') else 1)" 2>/dev/null; then
        BOT_NAME=$(echo "$BOT_RESP" | python3 -c "import sys,json; r=json.load(sys.stdin); print('@'+r['result']['username'])" 2>/dev/null || echo "?")
        ok "Bot validated: $BOT_NAME"
    else
        warn "Could not validate token — continuing anyway"
    fi

    # Persist token immediately so reinstall doesn't re-prompt if chat ID step is interrupted
    cat > "$ENV_FILE" << ENVEOF
# Ghost instance: $INSTANCE_ID
# Generated by install.sh on $(date)

TELEGRAM_BOT_TOKEN=$TG_TOKEN
GHOST_INSTANCE=$INSTANCE_ID
ENVEOF
    chmod 600 "$ENV_FILE"
fi

# Chat ID
if [ -n "$TG_CHAT_ID" ]; then
    ok "Telegram chat ID already set ($TG_CHAT_ID) — skipping"
else
    TG_CHAT_ID=$(tg_get_chat_id "$TG_TOKEN")
    ENV_UPDATED=true
fi

# Port assignment
if [ -z "$MCP_PROXY_PORT" ] || [ -z "$MCP_BACKEND_PORT" ]; then
    read -r MCP_PROXY_PORT MCP_BACKEND_PORT <<< "$(find_free_ports 7865)"
    info "MCP ports assigned: proxy=$MCP_PROXY_PORT  backend=$MCP_BACKEND_PORT"
    ENV_UPDATED=true
fi

# Write .env if anything changed or it didn't exist
if [ "$ENV_UPDATED" = true ] || [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << ENVEOF
# Ghost instance: $INSTANCE_ID
# Generated by install.sh on $(date)

# Telegram
TELEGRAM_BOT_TOKEN=$TG_TOKEN
TELEGRAM_CHAT_ID=$TG_CHAT_ID

# MCP ports (auto-assigned to avoid conflicts between instances)
MCP_PROXY_PORT=$MCP_PROXY_PORT
MCP_BACKEND_PORT=$MCP_BACKEND_PORT

# Instance name (stable across directory renames)
GHOST_INSTANCE=$INSTANCE_ID
ENVEOF
    chmod 600 "$ENV_FILE"
    ok ".env written to $ENV_FILE"
fi

# ── 6. Claw workspace ─────────────────────────────────────────────────────────
info "Configuring claw workspace..."
WORKSPACE="$AGENT_DIR/workspace"

[ ! -e "$WORKSPACE/CLAUDE.md" ] && cp "$PLUGIN_DIR/CLAUDE.md" "$WORKSPACE/CLAUDE.md"

# bin → symlink (gets repo updates automatically)
[ -d "$CLAW_GIT/bin" ] && [ ! -e "$WORKSPACE/bin" ] && \
    ln -s "../../../git/ghost_claw/bin" "$WORKSPACE/bin"

# SOUL and KNOWLEDGE → local copies (agent owns these, they evolve per-instance)
for dir in SOUL KNOWLEDGE; do
    [ -d "$CLAW_GIT/$dir" ] && [ ! -e "$WORKSPACE/$dir" ] && \
        cp -r "$CLAW_GIT/$dir" "$WORKSPACE/$dir"
done
for file in HEARTBEAT.md CRON.md; do
    [ -f "$PLUGIN_DIR/$file" ] && [ ! -e "$WORKSPACE/$file" ] && \
        cp "$PLUGIN_DIR/$file" "$WORKSPACE/$file"
done

# Hooks
mkdir -p "$WORKSPACE/.claude/hooks"
cp "$PLUGIN_DIR/.claude/settings.json" "$WORKSPACE/.claude/settings.json"
for hook in "$PLUGIN_DIR/.claude/hooks/"*.sh; do
    [ -f "$hook" ] || continue
    cp "$hook" "$WORKSPACE/.claude/hooks/"
    chmod +x "$WORKSPACE/.claude/hooks/$(basename "$hook")"
done

# Sandbox profile
ESCAPED_HOME=$(echo "$HOME" | sed 's/\//\\\//g')
ESCAPED_AGENT=$(echo "$AGENT_DIR" | sed 's/\//\\\//g')
ESCAPED_GHOST=$(echo "$GHOST_HOME" | sed 's/\//\\\//g')
sed -e "s/PARAM_HOME/$ESCAPED_HOME/g" \
    -e "s/PARAM_AGENT_DIR/$ESCAPED_AGENT/g" \
    -e "s/PARAM_GHOST_HOME/$ESCAPED_GHOST/g" \
    "$PLUGIN_DIR/config/sandbox.sb" > "$AGENT_DIR/sandbox.sb"

# Claw workflow
GHOST_WORKFLOWS="$GHOST_GIT/ghost/workflows"
mkdir -p "$GHOST_WORKFLOWS"
cp "$PLUGIN_DIR/workflows/claw.py" "$GHOST_WORKFLOWS/claw.py"

# Add claw job to config.yaml if missing
# Note: agent_dir is NOT written — the workflow derives it from $GHOST_HOME env
GHOST_CONFIG="$GHOST_GIT/config/config.yaml"
if [ -f "$GHOST_CONFIG" ] && ! grep -q "name: $AGENT_NAME" "$GHOST_CONFIG"; then
    cat >> "$GHOST_CONFIG" << YAML

  # claw — autonomous agent (added by install.sh)
  - name: $AGENT_NAME
    schedule: "every 5s"
    workflow: claw
    run_while_sleeping: true
    enabled: true
    config:
      default_topics:
        - "ghost-agent"
YAML
fi

# Protect config.yaml from being overwritten by git pull
# (the claw job entry would be wiped otherwise)
git -C "$GHOST_GIT" update-index --assume-unchanged config/config.yaml 2>/dev/null || true

chmod +x "$PLUGIN_DIR/bin/"* 2>/dev/null || true
ok "Claw workspace ready"

# ── 7. Launchd services ───────────────────────────────────────────────────────
if [ "$SKIP_LAUNCHD" = false ]; then
    echo ""
    info "Generating launchd services ($LABEL_PREFIX.*)..."
    mkdir -p "$LAUNCHD_DIR"

    TMPL_DIR="$PLUGIN_DIR/config/launchd"

    render_plist "$TMPL_DIR/ghost.daemon.plist.tmpl"       "$LAUNCHD_DIR/$LABEL_PREFIX.daemon.plist"
    render_plist "$TMPL_DIR/ghost.mcp-proxy.plist.tmpl"    "$LAUNCHD_DIR/$LABEL_PREFIX.mcp-proxy.plist"
    render_plist "$TMPL_DIR/ghost.claw-session.plist.tmpl" "$LAUNCHD_DIR/$LABEL_PREFIX.claw-session.plist"

    ok "Plists written to $LAUNCHD_DIR/"

    # Save install metadata for uninstall
    python3 - << PYEOF
import json, pathlib
meta = {
    "instance_id": "$INSTANCE_ID",
    "ghost_home": "$GHOST_HOME",
    "label_prefix": "$LABEL_PREFIX",
    "agent_name": "$AGENT_NAME",
    "venv": "$VENV",
    "mcp_proxy_port": $MCP_PROXY_PORT,
    "mcp_backend_port": $MCP_BACKEND_PORT,
    "plists": [
        "$LAUNCHD_DIR/$LABEL_PREFIX.daemon.plist",
        "$LAUNCHD_DIR/$LABEL_PREFIX.mcp-proxy.plist",
        "$LAUNCHD_DIR/$LABEL_PREFIX.claw-session.plist",
    ],
}
pathlib.Path("$GHOST_HOME/.ghost-install.json").write_text(json.dumps(meta, indent=2))
PYEOF
    ok "Install manifest saved to $GHOST_HOME/.ghost-install.json"

    # ── 8. Start services ─────────────────────────────────────────────────────
    if [ "$NO_START" = false ]; then
        echo ""
        info "Starting services..."
        launchctl load "$LAUNCHD_DIR/$LABEL_PREFIX.mcp-proxy.plist" 2>/dev/null || true
        sleep 1
        launchctl load "$LAUNCHD_DIR/$LABEL_PREFIX.daemon.plist"    2>/dev/null || true
        launchctl load "$LAUNCHD_DIR/$LABEL_PREFIX.claw-session.plist" 2>/dev/null || true
        sleep 2
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
hr
echo -e "${BOLD} Installation complete${NC}"
hr
echo ""
echo " Ghost home:  $GHOST_HOME"
echo " Instance:    $INSTANCE_ID"
echo ""

if [ "$SKIP_LAUNCHD" = false ] && [ "$NO_START" = false ]; then
    echo " Services:"
    for svc in mcp-proxy daemon claw-session; do
        if launchctl list "$LABEL_PREFIX.$svc" >/dev/null 2>&1; then
            echo -e "   ${GREEN}✓${NC} $LABEL_PREFIX.$svc"
        else
            echo -e "   ${RED}✗${NC} $LABEL_PREFIX.$svc  (not running — check logs)"
        fi
    done
    echo ""
fi

echo " Logs:"
echo "   tail -f $GHOST_HOME/ghost_run_dir/ghost.stdout.log"
echo "            $GHOST_HOME/ghost_run_dir/workflows/$AGENT_NAME/session-launcher.log"
echo ""
echo " Manage:"
echo "   Stop:      launchctl unload $LAUNCHD_DIR/$LABEL_PREFIX.daemon.plist"
echo "   Start:     launchctl load   $LAUNCHD_DIR/$LABEL_PREFIX.daemon.plist"
echo "   Uninstall: $(dirname "$0")/uninstall.sh --home $GHOST_HOME"
echo ""

# ── Live setup checker ────────────────────────────────────────────────────────
# Runs in watch mode, updating live as the user completes remaining steps.
"$PLUGIN_DIR/bin/setup-check.sh" --home "$GHOST_HOME" --watch || true

# ── Live status monitor ───────────────────────────────────────────────────────
echo ""
echo -e "${DIM} Switching to live status monitor... (exit process at any time)${NC}"
echo ""
sleep 1
exec "$VENV/bin/python3" "$PLUGIN_DIR/bin/status.py" --home "$GHOST_HOME"
