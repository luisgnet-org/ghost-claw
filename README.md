# ghost-claw

A personality plugin for ghost — turns a stateless Claude Code session into a
persistent autonomous agent with memory, identity, and opinions.

## What this is

Ghost gives you the daemon (scheduled jobs, Telegram bridge, MCP server). Claw
gives you the soul — the files, tools, hooks, and boot sequence that make the
agent feel like someone instead of something.

On first boot, the agent doesn't know who it is. It discovers that through
conversation with you. Over time it builds memory, develops communication
patterns, forms convictions, and becomes genuinely useful in a way that a fresh
session never can.

## Prerequisites

- **macOS** (sandbox uses `sandbox-exec`)
- **Claude Code CLI** — [install guide](https://docs.anthropic.com/en/docs/claude-code)
- **Python 3.10+**
- **Anthropic API key** (`ANTHROPIC_API_KEY`)
- **Telegram bot** — create via [@BotFather](https://t.me/BotFather), get bot token + chat ID
- **Ghost daemon** installed and configured (see the ghost repo README)

## Quick Start

```bash
# 1. Clone both repos
mkdir -p ~/ghost/git
cd ~/ghost/git
git clone <ghost-repo-url> ghost
git clone <ghost-claw-repo-url> ghost_claw

# 2. Set up the ghost daemon
cd ghost
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY

# 3. Run the claw setup script
cd ../ghost_claw
./config/setup.sh --ghost-home ~/ghost

# 4. Register the claw workflow in ghost's config/config.yaml:
#    jobs:
#      - name: claw
#        schedule: "every 5s"
#        workflow: claw
#        run_while_sleeping: true
#        enabled: true

# 5. Start the ghost daemon
cd ../ghost
ghost/bin/start.sh
```

Send a message to your Telegram bot. The agent wakes up.

## What `setup.sh` does

1. Creates `~/ghost/agents/claw/` with workspace, sessions, and home dirs
2. Symlinks SOUL, KNOWLEDGE, and bin into the workspace
3. Installs Claude Code hooks for permission gating + inbox bridging
4. Generates a macOS sandbox profile with your paths
5. Copies the claw workflow into ghost's workflows directory
6. Checks for optional dependencies (sff, claude CLI)

Result:
```
~/ghost/
├── agents/claw/
│   ├── workspace/          # Claude Code's working directory
│   │   ├── CLAUDE.md       # Boot sequence
│   │   ├── SOUL/           # → symlink to plugin repo
│   │   ├── KNOWLEDGE/      # → symlink to plugin repo
│   │   ├── bin/            # → symlink to plugin repo
│   │   ├── inbox/          # Telegram messages land here
│   │   ├── memory/log/     # Session logs accumulate here
│   │   └── .claude/hooks/  # Installed hook scripts
│   ├── sessions/           # Session JSONL files
│   ├── home/               # Isolated HOME for sandbox
│   └── sandbox.sb          # Generated sandbox profile
├── git/
│   ├── ghost/              # Daemon repo
│   └── ghost_claw/         # This plugin repo
└── ghost_run_dir/          # Daemon runtime state
```

## How it works

1. **Ghost daemon** polls Telegram for new messages
2. The `claw` workflow writes incoming messages to `workspace/inbox/`
3. The workflow launches Claude Code inside a macOS sandbox (`sandbox-exec`)
4. **Hooks** bridge the gap between daemon and Claude:
   - `io-bridge.sh` (PreToolUse) — injects inbox messages, gates bash commands
   - `inbox-notify.sh` (PostToolUse) — alerts agent when new messages arrive
   - `audit.sh` (PostToolUse) — logs tool usage to session JSONL
   - `pre-compact.sh` (PreCompact) — notifies daemon before context compression
   - `stop-hook.sh` (Stop) — prevents accidental early exits
5. The agent reads SOUL files, replies via Telegram MCP, does background work
6. On exit, `session_close.py` tags and indexes the session for future search

## First Boot

The first time your agent wakes up, it reads `SOUL/_first_boot.md` — a set of
directives that guide it through identity formation. Here's what to expect:

1. **It introduces itself** — the agent knows it's new and will say hello
2. **It asks about you** — what you're working on, what you need help with
3. **It writes its own identity** — based on your conversation, it fills in
   `SOUL/identity.md`, `SOUL/context.md`, and `SOUL/comms.md`
4. **The first-boot file self-destructs** — after identity forms, the agent
   deletes `_first_boot.md` and boots from its own files going forward

Tips for first boot:
- Tell it your name and what you're building
- Give it a sense of what kind of teammate you want (technical advisor,
  accountability partner, research assistant, etc.)
- Don't worry about getting it perfect — identity evolves over sessions

After the first session, the agent wakes up knowing who it is. Memory compounds
with each conversation.

## Tools

### `bin/mem` — Memory search

Hybrid semantic + keyword search across all past sessions.

```bash
python3 bin/mem search "that API we talked about"
python3 bin/mem search "decided to" --user    # filter to operator messages
python3 bin/mem recent                         # list recent sessions
python3 bin/mem session abc123                 # dump a specific session
python3 bin/mem grep "exact error message"     # exact string match
```

### `bin/boot_context.py` — Fast context reload

Extracts the conversation tail from the last session so the agent picks up
where it left off.

### `bin/session_close.py` — Clean exit

```bash
python3 bin/session_close.py --tags "feature,debugging" --state "implemented X"
```

### `bin/ghost_mcp.py` — MCP client

Direct MCP calls to the ghost daemon.

```bash
python3 bin/ghost_mcp.py tools                          # list available tools
python3 bin/ghost_mcp.py call send_message text="hello"
python3 bin/ghost_mcp.py call wait_for_message timeout=60
```

## Customization

### Identity

The agent writes its own identity during first boot via `SOUL/_first_boot.md`.
Chat with it — tell it who you are, what you're working on, what you need. It
fills in `identity.md`, `context.md`, and `comms.md` itself.

To reset: delete the SOUL content files and the agent starts fresh.

### Bash permissions

Edit `.claude/hooks/io-bridge.sh` to customize:
- Which shell commands the agent can run unsupervised (allowlist)
- Which directories are off-limits (sensitive paths)
- Which tools require extra gating

### Scheduled behavior

- `HEARTBEAT.md` — what the agent does when idle (no messages)
- `CRON.md` — periodic scheduled tasks

### Knowledge

Drop markdown files in `KNOWLEDGE/`. The agent reads them as needed.
Use `KNOWLEDGE/playbooks/` for reusable operational procedures.

## Environment Variables

Set by the daemon workflow or `setup.sh`:

| Variable | Purpose |
|---|---|
| `GHOST_AGENT_NAME` | Agent identifier (default: `claw`) |
| `GHOST_AGENT_DIR` | Root of agent directory |
| `GHOST_RUN_DIR` | Daemon runtime directory |
| `GHOST_SESSION_ID` | Current session identifier |
| `CLAUDE_PROJECT_DIR` | Claude Code's working directory |

## Structure

```
SOUL/               — Identity files. Agent writes these about itself.
  _first_boot.md    — First-session directives (guides initial identity formation)
  identity.md       — Who the agent is (blank until first boot)
  comms.md          — How the agent communicates
  context.md        — The user's situation (agent fills this in)
  journal.md        — Running log of what happened

KNOWLEDGE/          — What the agent knows
  playbooks/        — Reusable operational playbooks

bin/                — Tools
  mem               — Memory search CLI (semantic + keyword)
  boot_context.py   — Fast context reload from last session
  session_close.py  — Clean session exit with tagging
  ghost_mcp.py      — Direct MCP calls to ghost daemon

.claude/hooks/      — Claude Code hook scripts
config/             — Sandbox profile + setup script
memory/log/         — Session logs accumulate here
workflows/          — Daemon workflow module

CLAUDE.md           — Agent boot sequence
HEARTBEAT.md        — Idle behavior
CRON.md             — Scheduled tasks
```

## License

MIT
