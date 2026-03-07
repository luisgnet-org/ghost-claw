# ghost-claw — Agent Setup Guide

Operational reference for a Claude agent setting up or maintaining this system.
Read this instead of README.md when you're the one doing the work.

---

## What you're setting up

Two repos, one system:

- **ghost** (`GHOST_HOME/git/ghost`) — the daemon: Telegram polling, job scheduling, MCP server
- **ghost-claw** (`GHOST_HOME/git/ghost_claw`) — the plugin: agent identity, session launcher, hooks

Everything lives under a single root directory (`GHOST_HOME`, e.g. `~/ghost`).
The daemon runs as a launchd service. It wakes up Claude Code sessions in a sandbox
when Telegram messages arrive.

---

## Prerequisites — verify before starting

```bash
python3 --version          # need 3.10+
claude --version           # need Claude Code CLI installed
sandbox-exec 2>&1 | head   # must exist (macOS only — will say "Usage: sandbox-exec")
```

If `claude` is missing: https://docs.anthropic.com/en/docs/claude-code

You also need:
- **Telegram bot token** — create via `@BotFather` on Telegram (`/newbot`)
- **Anthropic API key** — from https://console.anthropic.com

---

## Fresh install

```bash
# Clone ghost-claw (you may already have it)
git clone <ghost-claw-repo-url> ghost_claw
cd ghost_claw

# Run installer — it clones ghost, creates venv, sets up everything
./install.sh --home ~/ghost
```

`install.sh` is interactive. When it asks for the Telegram bot token:
1. Paste the token from `@BotFather`
2. It validates the token against the API immediately
3. It then waits for you to send a message to detect the chat ID automatically

**To get the chat ID automatically (the installer does this for you):**
1. Create a Telegram Group (not a Channel)
2. Add your bot as Admin
3. Enable Topics: group → ··· → Edit → Topics → toggle ON
4. Send any message in the group
5. The installer detects it within seconds

If the auto-detect times out, find the chat ID manually:
```bash
# Replace TOKEN with your bot token — look for "chat":{"id": ...} in the output
curl -s "https://api.telegram.org/botTOKEN/getUpdates" | python3 -m json.tool | grep -A3 '"chat"'
```
Group chat IDs are negative numbers (e.g. `-1001234567890`).

---

## What install.sh creates

```
GHOST_HOME/
├── .env                         # credentials only — NO paths (portable)
├── .ghost-install.json          # install manifest (instance ID, ports, plist paths)
├── venv/                        # Python virtualenv
├── git/
│   ├── ghost/                   # daemon repo (cloned by installer)
│   └── ghost_claw/              # this repo (symlinked or cloned)
├── agents/claw/
│   ├── workspace/               # Claude Code runs here
│   │   ├── CLAUDE.md            # agent boot sequence (copied from plugin)
│   │   ├── SOUL/                # → ../../../git/ghost_claw/SOUL  (relative symlink)
│   │   ├── KNOWLEDGE/           # → ../../../git/ghost_claw/KNOWLEDGE
│   │   ├── bin/                 # → ../../../git/ghost_claw/bin
│   │   ├── inbox/               # incoming Telegram messages (JSON files)
│   │   ├── memory/log/          # session JSONL files accumulate here
│   │   └── .claude/hooks/       # hook scripts (copied from plugin)
│   ├── sessions/                # per-session dirs with JSONL + stderr logs
│   ├── home/                    # isolated $HOME for sandboxed Claude
│   └── sandbox.sb               # auto-regenerated at each session launch
└── ghost_run_dir/
    ├── ghost.stdout.log         # daemon stdout
    ├── ghost.stderr.log         # daemon stderr
    ├── mcp-proxy.stdout.log     # MCP proxy stdout
    ├── state.json               # shared daemon state
    ├── telegram/
    │   └── telegram.db          # Telegram message SQLite store
    └── workflows/claw/
        ├── .claude.pid          # session lockfile (PID of active Claude process)
        ├── audit/               # per-tool-call audit entries
        └── session-launcher.log # session launcher log
```

---

## Verifying the install

After `install.sh` completes, check services are running:

```bash
# All three should show a PID
launchctl list | grep com.ghost

# Check daemon started without errors
tail -20 ~/ghost/ghost_run_dir/ghost.stdout.log

# Check MCP proxy is up
curl -s http://[::1]:7865/mcp   # should return something (not "connection refused")
```

Check launchd service names — they're namespaced to avoid conflicts:
```bash
ls ~/Library/LaunchAgents/com.ghost.*.plist
# Expected: com.ghost.<instance>.daemon.plist
#           com.ghost.<instance>.mcp-proxy.plist
#           com.ghost.<instance>.claw-session.plist
```

The instance name defaults to `basename(GHOST_HOME)` — so `~/ghost` → `com.ghost.ghost.*`.

---

## The three services

| Service | launchd label | What it does |
|---|---|---|
| `daemon` | `com.ghost.<instance>.daemon` | Main loop. Polls Telegram, runs workflows, hosts MCP server on port `MCP_BACKEND_PORT` (default 7866). Restarts automatically (`KeepAlive`). |
| `mcp-proxy` | `com.ghost.<instance>.mcp-proxy` | Reverse proxy on `MCP_PROXY_PORT` (default 7865) → backend. Survives daemon restarts. Agents connect here. |
| `claw-session` | `com.ghost.<instance>.claw-session` | Runs every 1s. Checks inbox. If messages exist, acquires lockfile and launches Claude Code in sandbox. One session at a time. |

Start order matters: `mcp-proxy` should start before `daemon`.

---

## .env reference

The `.env` file lives at `GHOST_HOME/.env` — **credentials and ports only, no paths**.
Scripts derive `GHOST_HOME` from their own location at runtime.

```bash
# Required
TELEGRAM_BOT_TOKEN=          # from @BotFather
TELEGRAM_CHAT_ID=            # negative number for groups (e.g. -1001234567890)
ANTHROPIC_API_KEY=           # sk-ant-...

# Port numbers (auto-assigned during install to avoid conflicts)
MCP_PROXY_PORT=7865          # what agents/sessions connect to
MCP_BACKEND_PORT=7866        # what the daemon listens on internally

# Instance identity (stable across directory moves)
GHOST_INSTANCE=ghost         # used for launchd label prefix: com.ghost.<GHOST_INSTANCE>.*
```

Do not add `GHOST_HOME` or `GHOST_VENV` to `.env` — they're derived from file paths.

---

## How GHOST_HOME is derived (no hardcoding)

Each script self-locates:

**`ghost/bin/start.sh`** (at `GHOST_HOME/git/ghost/ghost/bin/start.sh`):
```
SCRIPT_DIR → GHOST_ROOT (ghost package) → REPO_ROOT (git/ghost) → GHOST_HOME (../../)
```

**`bin/claw-session.py`** (at `GHOST_HOME/git/ghost_claw/bin/claw-session.py`):
```python
_SELF_DIR = Path(__file__).parent           # ...ghost_claw/bin/
GHOST_HOME = _SELF_DIR.parent.parent.parent # ...ghost_claw/ → git/ → GHOST_HOME/
```
Note: no `.resolve()` — that would follow symlinks and break in dev mode.

**`workflows/claw.py`** — reads `os.environ["GHOST_HOME"]`, which `start.sh` exports after deriving it.

---

## Multiple instances (no conflicts)

Each instance gets a unique launchd label prefix and separate port pair:

```bash
./install.sh --home ~/ghost        # com.ghost.ghost.*   ports 7865/7866
./install.sh --home ~/ghost2       # com.ghost.ghost2.*  ports 7867/7868 (auto-found)
./install.sh --home ~/work-agent   # com.ghost.work-agent.* ports 7869/7870
```

If a conflict is detected, `install.sh` exits with instructions. To force a different
instance name: `./install.sh --home ~/ghost2 --instance-id myname`

---

## After moving/renaming GHOST_HOME

Scripts self-locate, so code and data work from the new path immediately.
Only launchd plists (which must contain absolute paths) need updating:

```bash
mv ~/ghost ~/myagent
~/myagent/git/ghost_claw/reinstall-launchd.sh
```

`reinstall-launchd.sh` self-locates from its own path, reads `.env` and
`.ghost-install.json`, removes old plists, generates new ones, reloads services.

---

## Managing services

```bash
INSTANCE=ghost   # change to your instance name

# Stop everything
launchctl unload ~/Library/LaunchAgents/com.ghost.$INSTANCE.daemon.plist
launchctl unload ~/Library/LaunchAgents/com.ghost.$INSTANCE.mcp-proxy.plist
launchctl unload ~/Library/LaunchAgents/com.ghost.$INSTANCE.claw-session.plist

# Start (in order)
launchctl load ~/Library/LaunchAgents/com.ghost.$INSTANCE.mcp-proxy.plist
launchctl load ~/Library/LaunchAgents/com.ghost.$INSTANCE.daemon.plist
launchctl load ~/Library/LaunchAgents/com.ghost.$INSTANCE.claw-session.plist

# Status
launchctl list | grep com.ghost.$INSTANCE

# Or use the daemon's stop script directly
~/ghost/git/ghost/ghost/bin/stop.sh
```

---

## Logs

| What | Where |
|---|---|
| Daemon stdout | `GHOST_HOME/ghost_run_dir/ghost.stdout.log` |
| Daemon stderr | `GHOST_HOME/ghost_run_dir/ghost.stderr.log` |
| MCP proxy | `GHOST_HOME/ghost_run_dir/mcp-proxy.stdout.log` |
| Session launcher | `GHOST_HOME/ghost_run_dir/workflows/claw/session-launcher.log` |
| Session JSONL | `GHOST_HOME/agents/claw/sessions/YYYY/MM/DD/session_*/session_*.jsonl` |
| Session stderr | same dir, `session_*.stderr` |

Tail everything at once:
```bash
tail -f ~/ghost/ghost_run_dir/ghost.stdout.log \
         ~/ghost/ghost_run_dir/workflows/claw/session-launcher.log
```

---

## Common failure modes

**Daemon exits immediately:**
```bash
cat ~/ghost/ghost_run_dir/ghost.stderr.log
# Usually: missing .env, wrong bot token, missing dependency
```

**Session launcher never starts a session:**
```bash
cat ~/ghost/ghost_run_dir/workflows/claw/session-launcher.log
# Check: inbox is empty? lockfile stale? sandbox profile missing?
ls ~/ghost/agents/claw/workspace/inbox/    # should have msg_*.json when messages arrive
ls ~/ghost/agents/claw/workspace/          # sandbox.sb regenerated here at launch
```

**Stale lockfile (session crashed without cleanup):**
```bash
rm ~/ghost/ghost_run_dir/workflows/claw/.claude.pid
```

**MCP proxy connection refused:**
```bash
launchctl list com.ghost.ghost.mcp-proxy   # check if running
# If PID shows 0, it crashed — check log
cat ~/ghost/ghost_run_dir/mcp-proxy.stdout.log
```

**Telegram bot not responding:**
```bash
# Verify bot token works
curl -s "https://api.telegram.org/botTOKEN/getMe"
# Check daemon is polling
grep -i telegram ~/ghost/ghost_run_dir/ghost.stdout.log | tail -5
```

**`sandbox-exec` permission errors:**
```bash
# sandbox.sb is regenerated at every session launch — check the template
cat ~/ghost/git/ghost_claw/config/sandbox.sb
# Generated profile (inspect what was actually used)
cat ~/ghost/agents/claw/sandbox.sb
```

---

## config.yaml — job configuration

Lives at `GHOST_HOME/git/ghost/config/config.yaml`. Hot-reloaded by the daemon.

The claw job added by `install.sh`:
```yaml
- name: claw
  schedule: "every 5s"
  workflow: claw
  run_while_sleeping: true
  enabled: true
  config:
    default_topic: "CLAW"      # Telegram topic name for agent messages
    # agent_dir: not needed — derived from $GHOST_HOME env
    # low_power_mode: false    # set true to conserve API quota
    # session_timeouts:
    #   heartbeat: 14400       # seconds (4h default)
    #   message: null          # null = unlimited
```

The `agent_dir` key is intentionally absent — the workflow derives it from `$GHOST_HOME`.

---

## Uninstall

```bash
# Remove launchd services + preserve data
./uninstall.sh --home ~/ghost

# Remove everything including GHOST_HOME
./uninstall.sh --home ~/ghost --remove-home
```

`uninstall.sh` reads `.ghost-install.json` to find the label prefix.
It does not touch any other ghost instances.

---

## Key files in this repo

```
install.sh            # main installer (run this first)
uninstall.sh          # clean removal
reinstall-launchd.sh  # re-register services after moving GHOST_HOME

CLAUDE.md             # agent boot sequence (copied to workspace at install)
HEARTBEAT.md          # what agent does when idle (copied to workspace)
CRON.md               # scheduled tasks (copied to workspace)

SOUL/                 # identity files — agent writes these, git-tracked
KNOWLEDGE/            # operational knowledge — drop .md files here
bin/
  claw-session.py     # session launcher (managed by launchd)
  mem                 # memory search CLI
  boot_context.py     # fast context reload from last session
  session_close.py    # clean session exit with tagging
  ghost_mcp.py        # direct MCP calls to daemon
workflows/
  claw.py             # daemon-side workflow (copied to ghost/workflows/ at install)
config/
  sandbox.sb          # sandbox-exec template (PARAM_HOME etc replaced at launch)
  launchd/            # plist templates (rendered by install.sh / reinstall-launchd.sh)
.claude/
  settings.json       # Claude Code settings (copied to workspace at install)
  hooks/              # hook scripts (copied to workspace at install)
    io-bridge.sh      # PreToolUse: injects inbox, gates bash commands
    inbox-notify.sh   # PostToolUse: alerts agent of new messages
    audit.sh          # PostToolUse: logs tool calls to session JSONL
    pre-compact.sh    # PreCompact: notifies daemon before context compression
    stop-hook.sh      # Stop: prevents premature session exit
```
