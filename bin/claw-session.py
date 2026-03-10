#!/usr/bin/env python3
"""
claw session launcher — runs Claude Code in a macOS sandbox.

Standalone process (typically managed by launchd) that watches the inbox
directory and launches Claude Code sessions inside a sandbox-exec sandbox.

Flow:
  1. Acquire lockfile (atomic O_CREAT|O_EXCL, exit if taken)
  2. Check inbox for content (exit if empty)
  3. Check rate limits (optional, via Anthropic OAuth)
  4. Place config files (CLAUDE.md, hooks, .mcp.json, SSH keys)
  5. Build prompt from inbox messages/heartbeats
  6. Notify daemon (REST API)
  7. Run Claude Code via sandbox-exec (proc.wait with configurable timeout)
  8. Cleanup + report

Configuration:
  Reads from ghost daemon's config.yaml under the claw job's config section:

    - name: claw
      config:
        default_topic: "MY_TOPIC"
        low_power_mode: false
        session_timeouts:
          heartbeat: 14400
          message: null

  Paths are derived from GHOST_HOME and AGENT_NAME environment variables,
  or fall back to sensible defaults.

Environment:
  GHOST_HOME   — root of ghost installation (default: ~/ghost)
  AGENT_NAME   — agent identifier (default: claw)
  MCP_PORT     — daemon MCP port (default: 7865)
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (derived from environment)
# ---------------------------------------------------------------------------

# Self-locate: this file is at GHOST_HOME/git/ghost_claw/bin/claw-session.py
# Walk up: bin/ → ghost_claw/ → git/ → GHOST_HOME/
# Note: do NOT call .resolve() — it would follow symlinks and give the wrong path
# when ghost_claw is symlinked (dev mode). Launchd always invokes via the literal path.
_SELF_DIR = Path(__file__).parent
_GHOST_HOME_DERIVED = _SELF_DIR.parent.parent.parent
GHOST_HOME = Path(os.environ.get("GHOST_HOME", str(_GHOST_HOME_DERIVED)))
AGENT_NAME = os.environ.get("AGENT_NAME", "claw")
GIT_ROOT = GHOST_HOME / "git" / "ghost"
AGENT_DIR = GHOST_HOME / "agents" / AGENT_NAME
WORKSPACE = AGENT_DIR / "workspace"
INBOX = WORKSPACE / "inbox"
RUNS_DIR = GHOST_HOME / "ghost_run_dir" / "workflows" / AGENT_NAME
LOCKFILE = RUNS_DIR / ".claude.pid"
SESSIONS_DIR = AGENT_DIR / "sessions"

CONFIG_DIR = GIT_ROOT / "config" / AGENT_NAME
BOOTSTRAP_DIR = CONFIG_DIR / "bootstrap"
SANDBOX_AUTH_DIR = CONFIG_DIR / ".sandbox-auth"
AGENT_HOME = AGENT_DIR / "home"  # Fake HOME for isolated Claude config

# Sandbox profile — regenerated at launch from template
SANDBOX_PROFILE = AGENT_DIR / "sandbox.sb"
SANDBOX_TEMPLATE = _SELF_DIR.parent / "config" / "sandbox.sb"   # ghost_claw/config/sandbox.sb

# Load .env from GHOST_HOME (launchd doesn't inherit daemon env)
_ENV_FILE = GHOST_HOME / ".env"
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

MCP_PORT = int(os.environ.get("MCP_PORT", "7865"))
DAEMON_API = f"http://[::1]:{MCP_PORT}"
OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
DAEMON_CONFIG = GIT_ROOT / "ghost" / "config" / "config.yaml"


def _load_agent_config() -> dict:
    """Read agent workflow config from daemon config.yaml."""
    try:
        import yaml
        cfg = yaml.safe_load(DAEMON_CONFIG.read_text())
        for job in cfg.get("jobs", []):
            if job.get("name") == AGENT_NAME:
                return job.get("config", {})
    except Exception:
        pass
    return {}


_agent_config = _load_agent_config()
DEFAULT_TOPIC = _agent_config.get("default_topic", AGENT_NAME.upper())
LOW_POWER_MODE = _agent_config.get("low_power_mode", False)

SESSION_TIMEOUT_BY_TRIGGER = {
    "heartbeat": _agent_config.get("session_timeouts", {}).get("heartbeat", 14400),
    "message":   _agent_config.get("session_timeouts", {}).get("message",   None),
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = RUNS_DIR / "session-launcher.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(f"{AGENT_NAME}-session")

# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------


def acquire_lock() -> int | None:
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCKFILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except FileExistsError:
        try:
            pid = int(LOCKFILE.read_text().strip())
            os.kill(pid, 0)
            return None
        except (ProcessLookupError, ValueError):
            logger.warning("Stale lockfile (dead PID), removing")
            LOCKFILE.unlink(missing_ok=True)
            return acquire_lock()
        except OSError:
            return None


def release_lock(fd: int):
    try:
        os.close(fd)
    except OSError:
        pass
    LOCKFILE.unlink(missing_ok=True)

# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


def has_inbox_content() -> bool:
    if not INBOX.exists():
        return False
    for pattern in ("msg_*.json", "heartbeat_*.json", "trigger_*.json"):
        if any(INBOX.glob(pattern)):
            return True
    return False


def inbox_topic() -> str | None:
    if not INBOX.exists():
        return None
    for f in sorted(INBOX.glob("msg_*.json")):
        try:
            return json.loads(f.read_text()).get("topic")
        except (json.JSONDecodeError, OSError):
            continue
    return None


def determine_trigger() -> str:
    if not INBOX.exists():
        return "heartbeat"
    if any(INBOX.glob("msg_*.json")) or any(INBOX.glob("trigger_*.json")):
        return "message"
    if any(INBOX.glob("heartbeat_*.json")):
        return "heartbeat"
    return "heartbeat"

# ---------------------------------------------------------------------------
# Rate limit check (optional — requires Anthropic OAuth credentials)
# ---------------------------------------------------------------------------


def _read_credentials() -> dict | None:
    """Read OAuth credentials from agent home."""
    creds_path = AGENT_HOME / ".claude" / ".credentials.json"
    if creds_path.exists():
        try:
            return json.loads(creds_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return None


def fetch_quota() -> dict | None:
    creds = _read_credentials()
    if not creds:
        return None
    try:
        token = creds.get("claudeAiOauth", {}).get("accessToken")
        if not token:
            return None
        req = urllib.request.Request(
            OAUTH_USAGE_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": f"{AGENT_NAME}-session/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning(f"Failed to fetch quota: {e}")
        return None


def is_rate_limited() -> bool:
    quota = fetch_quota()
    if not quota:
        return False
    for key in ("seven_day", "five_hour"):
        bucket = quota.get(key, {})
        if bucket.get("utilization", 0) >= 100:
            return True
    return False

# ---------------------------------------------------------------------------
# Daemon REST API
# ---------------------------------------------------------------------------


def api_post(path: str, body: dict) -> dict | None:
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{DAEMON_API}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning(f"API {path} failed: {e}")
        return None


def api_notify(text: str, topic_name: str | None = None):
    body = {"text": text}
    if topic_name:
        body["topic_name"] = topic_name
    api_post("/api/notify", body)


def api_report(exit_info, session_dir, quota_before, quota_after, session_label=None, topic_name=None):
    body = {
        "exit_info": exit_info,
        "session_dir": session_dir,
        "quota_before": quota_before,
        "quota_after": quota_after,
        "session_label": session_label,
    }
    if topic_name:
        body["topic_name"] = topic_name
    api_post("/api/report", body)


def api_set_all_sleeping():
    api_post("/api/set-all-sleeping", {})


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

STATE_PATH = GHOST_HOME / "ghost_run_dir" / "state.json"


def _regenerate_sandbox():
    """Regenerate sandbox.sb from template using current resolved paths.

    Called before every session so that moving GHOST_HOME doesn't require
    manual re-setup — the profile is always rebuilt from the template.
    """
    if not SANDBOX_TEMPLATE.exists():
        logger.warning(f"Sandbox template not found at {SANDBOX_TEMPLATE} — using existing profile")
        return
    profile = SANDBOX_TEMPLATE.read_text()
    profile = (profile
               .replace("PARAM_HOME", str(Path.home()))
               .replace("PARAM_AGENT_DIR", str(AGENT_DIR))
               .replace("PARAM_GHOST_HOME", str(GHOST_HOME)))
    SANDBOX_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    SANDBOX_PROFILE.write_text(profile)
    logger.debug(f"Sandbox profile regenerated → {SANDBOX_PROFILE}")


def _write_session_end_marker():
    try:
        state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
        shared = state.setdefault("shared", {})
        shared["claw_last_session_end"] = datetime.now().isoformat()
        STATE_PATH.write_text(json.dumps(state, indent=2, default=str))
    except Exception as e:
        logger.warning(f"Failed to write session end marker: {e}")

# ---------------------------------------------------------------------------
# Config file deployment
# ---------------------------------------------------------------------------


def place_config_files():
    """Deploy config from repo and seed workspace on first run."""
    fresh = not WORKSPACE.exists()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)

    if fresh:
        logger.info("Fresh workspace — seeding from bootstrap templates")
        for dirname in ("SOUL", "KNOWLEDGE"):
            src_dir = BOOTSTRAP_DIR / dirname
            if src_dir.exists():
                shutil.copytree(src_dir, WORKSPACE / dirname)
        for fname in ("HEARTBEAT.md", "CRON.md"):
            src = BOOTSTRAP_DIR / fname
            if src.exists():
                shutil.copy(src, WORKSPACE / fname)
        src = CONFIG_DIR / "CLAUDE.md"
        if src.exists():
            shutil.copy(src, WORKSPACE / "CLAUDE.md")

    # .claude/ settings + hooks
    ws_claude = WORKSPACE / ".claude"
    ws_claude.mkdir(parents=True, exist_ok=True)

    settings_src = CONFIG_DIR / ".claude" / "settings.json"
    if settings_src.exists():
        dest = ws_claude / "settings.json"
        if dest.exists():
            dest.chmod(0o644)
        shutil.copy(settings_src, dest)
        dest.chmod(0o444)

    hooks_src = CONFIG_DIR / ".claude" / "hooks"
    if hooks_src.exists():
        hooks_dir = ws_claude / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        for hook_file in hooks_src.iterdir():
            dest = hooks_dir / hook_file.name
            if dest.exists():
                dest.chmod(0o644)
            shutil.copy(hook_file, dest)
            dest.chmod(0o555)

    # .mcp.json
    mcp_template = CONFIG_DIR / ".mcp.json"
    if mcp_template.exists():
        mcp_text = mcp_template.read_text().replace("__MCP_PORT__", str(MCP_PORT))
    else:
        mcp_text = json.dumps({
            "mcpServers": {
                "telegram": {"type": "http", "url": f"http://[::1]:{MCP_PORT}/mcp"}
            }
        }, indent=2)
    dest = WORKSPACE / ".mcp.json"
    if dest.exists():
        dest.chmod(0o644)
    dest.write_text(mcp_text)
    dest.chmod(0o444)

    # Clean stale plan approval flag from previous sessions
    (WORKSPACE / ".plan_approved").unlink(missing_ok=True)

# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def read_heartbeat() -> str:
    ws_heartbeat = WORKSPACE / "HEARTBEAT.md"
    if ws_heartbeat.exists():
        return ws_heartbeat.read_text().strip()
    bootstrap_heartbeat = BOOTSTRAP_DIR / "HEARTBEAT.md"
    if bootstrap_heartbeat.exists():
        return bootstrap_heartbeat.read_text().strip()
    return (
        "Check SOUL/tasks.md and continue working on the highest priority item.\n"
        "If no tasks are pending, do proactive work."
    )


def determine_model(trigger: str, msgs: list[dict]) -> str:
    """Select Claude model based on trigger/message content."""
    return "claude-opus-4-6"


def build_initial_prompt(trigger: str) -> str:
    msgs = []
    for f in sorted(INBOX.glob("msg_*.json")):
        try:
            msgs.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue

    if msgs:
        lines = [f"[{m.get('from', 'user')}] {m['text']}" for m in msgs]
        return (
            "New messages:\n\n" + "\n".join(lines) + "\n\n"
            "Respond to these messages via Telegram first. "
            "Then check SOUL/tasks.md and work on the highest priority task. "
            "If no tasks are pending, do proactive work."
        )
    else:
        heartbeat_content = read_heartbeat()
        return (
            "No new messages. This is a heartbeat wakeup.\n\n"
            "## Standing Instructions (HEARTBEAT.md)\n\n"
            f"{heartbeat_content}\n\n"
            "Follow the instructions above. If nothing needs attention, "
            "update SOUL/journal.md and exit cleanly."
        )

# ---------------------------------------------------------------------------
# Session directory
# ---------------------------------------------------------------------------


def create_session_dir(session_id: str) -> Path:
    now = datetime.now()
    date_dir = SESSIONS_DIR / now.strftime("%Y/%m/%d")
    session_dir = date_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    latest_link = SESSIONS_DIR / "latest"
    latest_link.unlink(missing_ok=True)
    latest_link.symlink_to(session_dir)

    return session_dir

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    # 1. Acquire lockfile
    lock_fd = acquire_lock()
    if lock_fd is None:
        return

    session_proc = None

    try:
        # 2. Check inbox
        if not has_inbox_content():
            release_lock(lock_fd)
            return

        # 3. Determine trigger and topic
        trigger = determine_trigger()
        topic = inbox_topic() or DEFAULT_TOPIC
        logger.info(f"Session launcher woke — trigger: {trigger}, topic: {topic}")

        # 4. Rate limit check
        if is_rate_limited():
            logger.info("Rate limited, skipping session")
            quota = fetch_quota()
            api_report("rate limited", None, quota, quota, topic_name="SESSIONS")
            release_lock(lock_fd)
            return

        # 5. Regenerate sandbox profile from template (idempotent, handles moves)
        _regenerate_sandbox()
        if not SANDBOX_PROFILE.exists():
            logger.error(f"Sandbox profile missing and template not found: {SANDBOX_TEMPLATE}")
            release_lock(lock_fd)
            return

        # 6. Place config files
        place_config_files()

        # 7. Build prompt + select model
        prompt = build_initial_prompt(trigger)
        if LOW_POWER_MODE:
            prompt = (
                "SYSTEM: Low power mode is active (quota nearly exhausted). "
                "Be concise and conservative — respond to messages briefly, skip "
                "proactive work, avoid spawning subagents or long tasks. "
                "After handling any messages, prefer to call wait_for_message(timeout=3600) "
                "and idle rather than doing background work.\n\n"
            ) + prompt
        inbox_msgs = []
        if INBOX.exists():
            for f in sorted(INBOX.glob("msg_*.json")):
                try:
                    inbox_msgs.append(json.loads(f.read_text()))
                except (json.JSONDecodeError, OSError):
                    continue
        model = determine_model(trigger, inbox_msgs)
        logger.info(f"Model: {model}")

        # 8. Create session ID
        session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S")
        try:
            seq_num = sum(
                1 for d in SESSIONS_DIR.rglob("session_*") if d.is_dir()
            )
        except Exception:
            seq_num = None
        short_id = f"#{seq_num}" if seq_num is not None else session_id.replace("session_", "")

        # 9. Notify daemon
        api_notify(f"\U0001f7e2 awake ({trigger}) [{short_id}]", topic_name="SESSIONS")

        # 10. Snapshot quota
        quota_before = fetch_quota()
        session_dir = create_session_dir(session_id)
        stderr_path = session_dir / f"{session_id}.stderr"
        jsonl_path = session_dir / f"{session_id}.jsonl"

        # 11. Write prompt to temp file
        prompt_file = Path(f"/tmp/.{AGENT_NAME}-prompt-{os.getpid()}.txt")
        prompt_file.write_text(prompt)

        # 12. Build command — sandbox-exec wrapping claude
        env = os.environ.copy()
        env["CLAW_SESSION_ID"] = session_id
        env["CLAW_JSONL_PATH"] = str(jsonl_path)
        # Unset Claude env vars to avoid nested session detection
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        env["HOME"] = str(AGENT_HOME)

        # Memory isolation: propagate USE_HOST_CLAUDE_SESSIONS from .env
        # Default false — subject agents can't read host ~/.claude/ sessions
        if "USE_HOST_CLAUDE_SESSIONS" not in env:
            env["USE_HOST_CLAUDE_SESSIONS"] = "false"

        # Ensure claude is findable: launchd starts with a stripped PATH that
        # may not include wherever the user installed claude (e.g. ~/.local/bin,
        # /opt/homebrew/bin). Prepend common install locations.
        extra_paths = [
            str(Path.home() / ".local" / "bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
        ]
        current_path = env.get("PATH", "/usr/bin:/bin")
        env["PATH"] = ":".join(extra_paths) + ":" + current_path

        # Resolve full path to claude binary so sandbox-exec doesn't rely on PATH lookup
        claude_bin = shutil.which("claude", path=env["PATH"]) or "claude"

        cmd = [
            "sandbox-exec", "-f", str(SANDBOX_PROFILE),
            claude_bin,
            "--dangerously-skip-permissions",
            "--print",
            "--verbose",
            "--output-format", "stream-json",
            "--model", model,
            "-p", prompt,
        ]

        stdout_fh = open(jsonl_path, "wb")
        stderr_fh = open(stderr_path, "wb")

        try:
            session_proc = subprocess.Popen(
                cmd,
                stdout=stdout_fh,
                stderr=stderr_fh,
                cwd=str(WORKSPACE),
                env=env,
                start_new_session=True,
            )

            # Update lockfile with session PID
            os.lseek(lock_fd, 0, os.SEEK_SET)
            os.ftruncate(lock_fd, 0)
            os.write(lock_fd, str(session_proc.pid).encode())

            logger.info(f"Session {session_id} started (pid={session_proc.pid})")

            # 13. Wait with trigger-specific timeout
            max_seconds = SESSION_TIMEOUT_BY_TRIGGER.get(trigger, 14400)
            try:
                session_proc.wait(timeout=max_seconds)
            except subprocess.TimeoutExpired:
                wrapup = WORKSPACE / "WRAP_UP.md"
                wrapup.write_text(
                    "SYSTEM: Session timeout approaching. You have ~60 seconds.\n"
                    "Save your work to SOUL/journal.md, then exit."
                )
                logger.info(f"Session {session_id} timeout, grace period")
                try:
                    session_proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    session_proc.terminate()
                    try:
                        session_proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        session_proc.kill()
                        session_proc.wait()
                    logger.info(f"Session {session_id} terminated after grace period")

            exit_info = f"exit={session_proc.returncode}"
            logger.info(f"Session {session_id} ended ({exit_info})")

        finally:
            stdout_fh.close()
            stderr_fh.close()
            prompt_file.unlink(missing_ok=True)

        session_proc = None

        # --- Cleanup ---
        (WORKSPACE / "WRAP_UP.md").unlink(missing_ok=True)
        (WORKSPACE / "WRAP_UP_ACK.md").unlink(missing_ok=True)

        quota_after = fetch_quota()

        # Build human-readable exit message for SESSIONS topic
        code = session_proc.returncode if session_proc else None
        exit_descriptions = {
            0:   "clean exit",
            1:   "error (check if Claude is authenticated)",
            130: "interrupted (SIGINT)",
            137: "killed (SIGKILL)",
            143: "stopped (SIGTERM)",
        }
        exit_label = exit_descriptions.get(code, f"exit {code}")
        quota_str = ""
        if isinstance(quota_before, dict) and isinstance(quota_after, dict):
            try:
                pct = quota_after.get("five_hour", {}).get("utilization", 0)
                quota_str = f"  quota {pct}%"
            except Exception:
                pass
        api_notify(
            f"\U0001f534 done [{short_id}]  {exit_label}{quota_str}",
            topic_name="SESSIONS",
        )
        api_set_all_sleeping()
        _write_session_end_marker()

    except Exception as e:
        logger.error(f"Session launcher error: {e}", exc_info=True)
    finally:
        if INBOX.exists():
            for pattern in ("heartbeat_*.json", "trigger_*.json",
                            "heartbeat_*.json.read", "trigger_*.json.read"):
                for f in INBOX.glob(pattern):
                    f.unlink(missing_ok=True)
            # Mark any unconsumed messages as read (not deleted — kept for audit)
            for f in INBOX.glob("msg_*.json"):
                try:
                    f.rename(f.parent / (f.name + ".read"))
                except OSError:
                    pass

        if session_proc is not None and session_proc.poll() is None:
            logger.warning("Launcher exiting but session still alive — leaving lockfile")
            try:
                os.close(lock_fd)
            except OSError:
                pass
        else:
            release_lock(lock_fd)
            logger.info("Session launcher finished")


if __name__ == "__main__":
    main()
