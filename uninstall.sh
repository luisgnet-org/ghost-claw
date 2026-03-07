#!/bin/bash
# uninstall.sh — Remove a ghost+claw instance
#
# Usage:
#   ./uninstall.sh --home ~/ghost2 [--remove-home]
#
# Options:
#   --home DIR       Ghost home directory (required)
#   --remove-home    Also delete the entire GHOST_HOME directory

set -euo pipefail

GHOST_HOME=""
REMOVE_HOME=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --home)        GHOST_HOME="$2"; shift 2 ;;
        --remove-home) REMOVE_HOME=true; shift ;;
        --help|-h)
            sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

GHOST_HOME="${GHOST_HOME/#\~/$HOME}"

if [ -z "$GHOST_HOME" ]; then
    echo "Usage: ./uninstall.sh --home ~/ghost2 [--remove-home]"
    exit 1
fi

INSTALL_FILE="$GHOST_HOME/.ghost-install.json"
if [ ! -f "$INSTALL_FILE" ]; then
    echo "Error: no install manifest found at $INSTALL_FILE"
    echo "Cannot determine which launchd services to remove."
    echo ""
    echo "To manually clean up, look for plist files in ~/Library/LaunchAgents/"
    echo "matching your instance name, then:"
    echo "  launchctl unload <plist>"
    echo "  rm <plist>"
    exit 1
fi

LABEL_PREFIX=$(python3 -c "import json; d=json.load(open('$INSTALL_FILE')); print(d['label_prefix'])")
INSTANCE_ID=$(python3 -c "import json; d=json.load(open('$INSTALL_FILE')); print(d['instance_id'])")
LAUNCHD_DIR="$HOME/Library/LaunchAgents"

echo ""
echo "Uninstalling ghost instance: $INSTANCE_ID"
echo "  Label prefix: $LABEL_PREFIX"
echo ""

for svc in daemon mcp-proxy claw-session; do
    PLIST="$LAUNCHD_DIR/$LABEL_PREFIX.$svc.plist"
    if [ -f "$PLIST" ]; then
        printf "  Stopping %-30s" "$LABEL_PREFIX.$svc..."
        launchctl unload "$PLIST" 2>/dev/null || true
        rm "$PLIST"
        echo " ✓"
    fi
done

if [ "$REMOVE_HOME" = true ]; then
    echo ""
    echo "  Removing $GHOST_HOME..."
    rm -rf "$GHOST_HOME"
    echo "  ✓ Removed"
else
    echo ""
    echo "  Ghost home preserved: $GHOST_HOME"
    echo "  To fully remove:      rm -rf $GHOST_HOME"
    echo "  Or rerun with:        --remove-home"
fi

echo ""
echo "✓ Uninstall complete"
echo ""
