#!/bin/bash
# reinstall-launchd.sh — Re-register launchd services after moving GHOST_HOME
#
# Run this after renaming or moving the ghost home directory:
#
#   mv ~/ghost2 ~/myagent
#   ~/myagent/git/ghost_claw/reinstall-launchd.sh
#
# This script self-locates from its own path — no arguments needed.
# It reads .env for port numbers and .ghost-install.json for the instance name,
# removes the old (now broken) plists, and registers fresh ones.

set -euo pipefail

# Self-locate: this file is at GHOST_HOME/git/ghost_claw/reinstall-launchd.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHOST_HOME="$(cd "$SCRIPT_DIR/../.." && pwd)"   # ghost_claw/ → git/ → GHOST_HOME/

ENV_FILE="$GHOST_HOME/.env"
INSTALL_FILE="$GHOST_HOME/.ghost-install.json"
LAUNCHD_DIR="$HOME/Library/LaunchAgents"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
info() { echo -e "${BLUE}→${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

echo ""
echo "Re-registering launchd services for ghost at:"
echo "  $GHOST_HOME"
echo ""

# Read install manifest for instance/label info
[ -f "$INSTALL_FILE" ] || err "No .ghost-install.json found at $GHOST_HOME — was this installed with install.sh?"
[ -f "$ENV_FILE" ]     || err "No .env found at $GHOST_HOME"

LABEL_PREFIX=$(python3 -c "import json; d=json.load(open('$INSTALL_FILE')); print(d['label_prefix'])")
INSTANCE_ID=$(python3 -c "import json; d=json.load(open('$INSTALL_FILE')); print(d['instance_id'])")
AGENT_NAME=$(python3 -c "import json; d=json.load(open('$INSTALL_FILE')); print(d['agent_name'])")

# Load port numbers from .env
set -a; source "$ENV_FILE"; set +a
MCP_PROXY_PORT="${MCP_PROXY_PORT:-7865}"
MCP_BACKEND_PORT="${MCP_BACKEND_PORT:-7866}"

VENV="$GHOST_HOME/venv"
AGENT_DIR="$GHOST_HOME/agents/$AGENT_NAME"

info "Instance:     $INSTANCE_ID"
info "Label prefix: $LABEL_PREFIX"
info "MCP ports:    proxy=$MCP_PROXY_PORT  backend=$MCP_BACKEND_PORT"
echo ""

# Unload and remove old plists
info "Removing old plists..."
for svc in daemon mcp-proxy claw-session; do
    PLIST="$LAUNCHD_DIR/$LABEL_PREFIX.$svc.plist"
    if [ -f "$PLIST" ]; then
        launchctl unload "$PLIST" 2>/dev/null || true
        rm "$PLIST"
        ok "Removed $LABEL_PREFIX.$svc"
    fi
done

# Render and register fresh plists
info "Generating fresh plists with current paths..."
TMPL_DIR="$SCRIPT_DIR/config/launchd"

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

render_plist "$TMPL_DIR/ghost.daemon.plist.tmpl"       "$LAUNCHD_DIR/$LABEL_PREFIX.daemon.plist"
render_plist "$TMPL_DIR/ghost.mcp-proxy.plist.tmpl"    "$LAUNCHD_DIR/$LABEL_PREFIX.mcp-proxy.plist"
render_plist "$TMPL_DIR/ghost.claw-session.plist.tmpl" "$LAUNCHD_DIR/$LABEL_PREFIX.claw-session.plist"
ok "Plists written"

# Update install manifest with new paths
python3 - << PYEOF
import json, pathlib
path = pathlib.Path("$INSTALL_FILE")
meta = json.loads(path.read_text())
meta["ghost_home"] = "$GHOST_HOME"
meta["venv"] = "$VENV"
meta["plists"] = [
    "$LAUNCHD_DIR/$LABEL_PREFIX.daemon.plist",
    "$LAUNCHD_DIR/$LABEL_PREFIX.mcp-proxy.plist",
    "$LAUNCHD_DIR/$LABEL_PREFIX.claw-session.plist",
]
path.write_text(json.dumps(meta, indent=2))
PYEOF
ok "Install manifest updated"

# Load fresh plists
info "Starting services..."
launchctl load "$LAUNCHD_DIR/$LABEL_PREFIX.mcp-proxy.plist" 2>/dev/null || true
sleep 1
launchctl load "$LAUNCHD_DIR/$LABEL_PREFIX.daemon.plist"    2>/dev/null || true
launchctl load "$LAUNCHD_DIR/$LABEL_PREFIX.claw-session.plist" 2>/dev/null || true
sleep 2

for svc in daemon mcp-proxy claw-session; do
    if launchctl list "$LABEL_PREFIX.$svc" >/dev/null 2>&1; then
        ok "$LABEL_PREFIX.$svc"
    else
        echo "  ! $LABEL_PREFIX.$svc — not running (check logs)"
    fi
done

echo ""
echo "Done. Ghost is running from: $GHOST_HOME"
echo ""
