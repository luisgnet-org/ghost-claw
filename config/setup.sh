#!/bin/bash
# setup.sh — Set up ghost-claw agent environment
#
# Creates the directory structure, installs the sandbox profile,
# and sets up launchd for session auto-launch.
#
# Usage:
#   ./config/setup.sh [--agent-name NAME] [--ghost-home DIR]
#
# Defaults:
#   --agent-name claw
#   --ghost-home ~/ghost

set -euo pipefail

AGENT_NAME="claw"
GHOST_HOME="$HOME/ghost"

while [[ $# -gt 0 ]]; do
    case $1 in
        --agent-name) AGENT_NAME="$2"; shift 2 ;;
        --ghost-home) GHOST_HOME="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

AGENT_DIR="$GHOST_HOME/agents/$AGENT_NAME"
RUN_DIR="$GHOST_HOME/ghost_run_dir/workflows/$AGENT_NAME"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== ghost-claw setup ==="
echo "Agent name:  $AGENT_NAME"
echo "Ghost home:  $GHOST_HOME"
echo "Agent dir:   $AGENT_DIR"
echo "Plugin dir:  $PLUGIN_DIR"
echo ""

# 1. Create directory structure
echo "Creating directories..."
mkdir -p "$AGENT_DIR"/{workspace,sessions,home}
mkdir -p "$AGENT_DIR/workspace/inbox"
mkdir -p "$AGENT_DIR/workspace/memory/log"
mkdir -p "$RUN_DIR/audit"

# 2. Symlink or copy the plugin into workspace
if [ ! -e "$AGENT_DIR/workspace/CLAUDE.md" ]; then
    echo "Linking plugin files into workspace..."
    # Copy CLAUDE.md and key files (these need to be at workspace root)
    cp "$PLUGIN_DIR/CLAUDE.md" "$AGENT_DIR/workspace/CLAUDE.md"
    # Symlink directories that should stay in sync with the plugin repo
    for dir in SOUL KNOWLEDGE bin; do
        if [ -d "$PLUGIN_DIR/$dir" ] && [ ! -e "$AGENT_DIR/workspace/$dir" ]; then
            ln -s "$PLUGIN_DIR/$dir" "$AGENT_DIR/workspace/$dir"
        fi
    done
    # Copy files that the agent will modify
    for file in HEARTBEAT.md CRON.md; do
        if [ -f "$PLUGIN_DIR/$file" ] && [ ! -e "$AGENT_DIR/workspace/$file" ]; then
            cp "$PLUGIN_DIR/$file" "$AGENT_DIR/workspace/$file"
        fi
    done
fi

# 3. Copy hooks into workspace .claude/ directory
echo "Installing hooks..."
mkdir -p "$AGENT_DIR/workspace/.claude/hooks"
cp "$PLUGIN_DIR/.claude/settings.json" "$AGENT_DIR/workspace/.claude/settings.json"
for hook in "$PLUGIN_DIR/.claude/hooks/"*.sh; do
    [ -f "$hook" ] || continue
    cp "$hook" "$AGENT_DIR/workspace/.claude/hooks/"
    chmod +x "$AGENT_DIR/workspace/.claude/hooks/$(basename "$hook")"
done

# 4. Generate sandbox profile with resolved paths
echo "Generating sandbox profile..."
ESCAPED_HOME=$(echo "$HOME" | sed 's/\//\\\//g')
ESCAPED_AGENT_DIR=$(echo "$AGENT_DIR" | sed 's/\//\\\//g')
ESCAPED_GHOST_HOME=$(echo "$GHOST_HOME" | sed 's/\//\\\//g')

sed -e "s/PARAM_HOME/$ESCAPED_HOME/g" \
    -e "s/PARAM_AGENT_DIR/$ESCAPED_AGENT_DIR/g" \
    -e "s/PARAM_GHOST_HOME/$ESCAPED_GHOST_HOME/g" \
    "$PLUGIN_DIR/config/sandbox.sb" > "$AGENT_DIR/sandbox.sb"

# 5. Install workflow into ghost daemon
GHOST_WORKFLOWS="$GHOST_HOME/git/ghost/ghost/workflows"
if [ -d "$GHOST_WORKFLOWS" ] && [ -f "$PLUGIN_DIR/workflows/claw.py" ]; then
    echo "Installing claw workflow into ghost daemon..."
    cp "$PLUGIN_DIR/workflows/claw.py" "$GHOST_WORKFLOWS/claw.py"
else
    echo "NOTE: Could not find ghost daemon at $GHOST_WORKFLOWS"
    echo "      Copy workflows/claw.py into your ghost/workflows/ directory manually."
fi

# 6. Make bin/ scripts executable
chmod +x "$PLUGIN_DIR/bin/"* 2>/dev/null || true

# 7. Check for optional dependencies
echo ""
echo "Checking dependencies..."
if python3 -c "import sff" 2>/dev/null; then
    echo "  ✓ sff (semantic search) — installed"
else
    echo "  ✗ sff (semantic search) — not installed"
    echo "    bin/mem will use keyword-only search. For semantic search:"
    echo "    pip install sff"
fi
if command -v claude &>/dev/null; then
    echo "  ✓ claude CLI — installed"
else
    echo "  ✗ claude CLI — not found"
    echo "    Install: https://docs.anthropic.com/en/docs/claude-code"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Make sure the ghost daemon is configured (.env with bot token + API key)"
echo "  2. Add claw to ghost's config/config.yaml:"
echo "       jobs:"
echo "         - name: claw"
echo "           schedule: \"every 5s\""
echo "           workflow: claw"
echo "           run_while_sleeping: true"
echo "           enabled: true"
echo "  3. Start the daemon: ghost/bin/start.sh"
echo "  4. Send a message to your Telegram bot — the agent wakes up"
echo ""
echo "To run Claude Code in the sandbox manually:"
echo "  sandbox-exec -f $AGENT_DIR/sandbox.sb \\"
echo "    env HOME=$AGENT_DIR/home \\"
echo "    claude --project-dir $AGENT_DIR/workspace"
