#!/usr/bin/env python3
"""
Ghost + Claw system status monitor.

Shows live health of the entire pipeline:
  services → telegram → inbox → session → replies

Usage:
  bin/status.py                    # loop, refresh every 3s
  bin/status.py --once             # print once and exit
  bin/status.py --home ~/myghost  # explicit GHOST_HOME
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Self-locate ────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).parent
_GHOST_HOME_DEFAULT = _SCRIPT_DIR.parent.parent.parent  # bin/ → ghost_claw/ → git/ → GHOST_HOME/

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
YELLOW = "\033[1;33m"
CYAN   = "\033[0;36m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
NC     = "\033[0m"

OK   = f"{GREEN}✓{NC}"
FAIL = f"{RED}✗{NC}"
WARN = f"{YELLOW}~{NC}"
WAIT = f"{YELLOW}○{NC}"


def _ago(ts: float | None) -> str:
    """Human-readable time since ts (unix or isoformat)."""
    if ts is None:
        return "never"
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts).timestamp()
        except Exception:
            return "?"
    secs = int(time.time() - ts)
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        h, m = divmod(secs // 60, 60)
        return f"{h}h{m:02d}m ago"
    return f"{secs // 86400}d ago"


def _launchctl_status(label: str) -> tuple[str, int | None, int | None]:
    """Returns (state, pid, exit_code). state: 'running'|'stopped'|'unknown'."""
    try:
        r = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return "stopped", None, None
        data = {}
        for line in r.stdout.splitlines():
            line = line.strip().rstrip(";")
            if " = " in line:
                k, _, v = line.partition(" = ")
                data[k.strip().strip('"')] = v.strip().strip('"')
        pid = int(data["PID"]) if data.get("PID", "0") not in ("0", "") else None
        exit_code = int(data.get("LastExitStatus", "0") or "0")
        state = "running" if pid else "stopped"
        return state, pid, exit_code
    except Exception:
        return "unknown", None, None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def build_status(ghost_home: Path) -> list[str]:
    lines: list[str] = []
    label_prefix = f"com.ghost.{ghost_home.name}"

    def h(title: str):
        lines.append(f"\n {BOLD}{title}{NC}")
        lines.append(f" {'─' * 52}")

    def row(icon: str, label: str, value: str = "", note: str = ""):
        label_col = f"{label:<28}"
        val_col   = f"{value:<18}"
        note_col  = f"{DIM}{note}{NC}" if note else ""
        lines.append(f"  {icon}  {label_col}{val_col}{note_col}")

    # ── 1. Services ───────────────────────────────────────────────────────────
    h("Services")
    for svc in ("daemon", "mcp-proxy", "claw-session"):
        label = f"{label_prefix}.{svc}"
        state, pid, exit_code = _launchctl_status(label)
        if state == "running":
            row(OK, svc, "running", f"pid {pid}")
        elif state == "stopped":
            note = f"exit {exit_code}" if exit_code else "not running"
            row(FAIL if svc != "claw-session" else WAIT, svc, "stopped", note)
        else:
            row(WARN, svc, "unknown", "launchctl lookup failed")

    # MCP server (check port directly)
    mcp_port_path = ghost_home / ".env"
    mcp_port = 7870
    if mcp_port_path.exists():
        for line in mcp_port_path.read_text().splitlines():
            if line.startswith("MCP_BACKEND_PORT="):
                try:
                    mcp_port = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
    mcp_ok = False
    for host in ("127.0.0.1", "::1", "localhost"):
        try:
            import socket
            s = socket.create_connection((host, mcp_port), timeout=0.5)
            s.close()
            mcp_ok = True
            break
        except Exception:
            pass
    if mcp_ok:
        row(OK, "MCP server", "listening", f"port {mcp_port}")
    else:
        row(FAIL, "MCP server", "not reachable", f"port {mcp_port}")

    # ── 2. Daemon ─────────────────────────────────────────────────────────────
    h("Daemon")
    state_path = ghost_home / "ghost_run_dir" / "state.json"
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            pass

    last_run_map = state.get("last_run", {})
    claw_last = last_run_map.get("claw")
    if claw_last:
        row(OK, "Claw workflow last run", "", _ago(claw_last))
    else:
        row(FAIL, "Claw workflow", "never run", "check daemon + config.yaml")

    log_path = ghost_home / "ghost_run_dir" / "ghost.log"
    if log_path.exists():
        mtime = log_path.stat().st_mtime
        age_s = time.time() - mtime
        icon = OK if age_s < 30 else (WARN if age_s < 120 else FAIL)
        row(icon, "Daemon log activity", "", _ago(mtime))
    else:
        row(FAIL, "Daemon log", "not found", str(log_path))

    # ── 3. Telegram DB ────────────────────────────────────────────────────────
    h("Telegram")
    db_path = ghost_home / "ghost_run_dir" / "telegram" / "telegram.db"
    db_ok = False
    topics_map: dict[int, str] = {}  # topic_id → name
    last_user_msg_ts: float | None = None
    last_user_msg_text: str = ""
    total_events = 0
    bot_user_id: int | None = None

    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*) FROM events")
            total_events = cur.fetchone()[0]

            cur.execute("SELECT topic_id, topic_name FROM topics ORDER BY last_used DESC")
            for r in cur.fetchall():
                topics_map[r["topic_id"]] = r["topic_name"]

            # Find bot user id (most frequent sender with is_bot heuristic)
            cur.execute("""
                SELECT user_id, user_name, COUNT(*) as cnt FROM events
                WHERE event_type='message' AND text=''
                GROUP BY user_id ORDER BY cnt DESC LIMIT 1
            """)
            r = cur.fetchone()
            if r:
                bot_user_id = r["user_id"]

            # Last non-bot message
            if bot_user_id:
                cur.execute("""
                    SELECT text, timestamp FROM events
                    WHERE event_type='message' AND user_id != ? AND text != ''
                    ORDER BY update_id DESC LIMIT 1
                """, (bot_user_id,))
            else:
                cur.execute("""
                    SELECT text, timestamp FROM events
                    WHERE event_type='message' AND text != ''
                    ORDER BY update_id DESC LIMIT 1
                """)
            r = cur.fetchone()
            if r:
                last_user_msg_ts = r["timestamp"]
                last_user_msg_text = (r["text"] or "")[:40]

            conn.close()
            db_ok = True
        except Exception as e:
            row(FAIL, "Telegram DB", "error", str(e)[:40])

    if db_ok:
        row(OK, "Telegram DB", f"{total_events} events", f"last user msg {_ago(last_user_msg_ts)}")
        if last_user_msg_text:
            row("  ", "Last message", "", f'"{last_user_msg_text}"')
        if topics_map:
            for tid, tname in list(topics_map.items())[:4]:
                row("  ", f"Topic '{tname}'", f"id={tid}", "")
    else:
        row(FAIL, "Telegram DB", "not found", str(db_path))

    # ── 4. Claw pipeline ──────────────────────────────────────────────────────
    h("Claw Pipeline")

    # Cursors vs DB
    shared = state.get("shared", {})
    cursors: dict = shared.get("claw_topic_cursors", {}) or {}

    if db_ok and cursors and db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            for topic_name, cursor_uid in cursors.items():
                # Find topic_id for this name
                tid = next((k for k, v in topics_map.items() if v == topic_name), None)
                if tid is None:
                    row(WARN, f"Cursor '{topic_name}'", "topic id unknown", f"@{cursor_uid}")
                    continue
                # Count unread user messages
                if bot_user_id:
                    cur.execute("""
                        SELECT COUNT(*) FROM events
                        WHERE topic_id=? AND event_type='message'
                        AND user_id != ? AND text != ''
                        AND update_id > ?
                    """, (tid, bot_user_id, cursor_uid))
                else:
                    cur.execute("""
                        SELECT COUNT(*) FROM events
                        WHERE topic_id=? AND event_type='message'
                        AND text != '' AND update_id > ?
                    """, (tid, cursor_uid))
                unread = cur.fetchone()[0]
                if unread:
                    row(WARN, f"Cursor '{topic_name}'", f"{unread} unread", f"cursor @{cursor_uid}")
                else:
                    row(OK, f"Cursor '{topic_name}'", "up to date", f"@{cursor_uid}")
            conn.close()
        except Exception as e:
            row(WARN, "Cursor check", "error", str(e)[:40])
    elif not cursors:
        row(WAIT, "Cursors", "not initialized", "claw hasn't run yet")

    # Inbox
    inbox_path = ghost_home / "agents" / "claw" / "workspace" / "inbox"
    pending_msgs = sorted(inbox_path.glob("msg_*.json")) if inbox_path.exists() else []
    pending_hb   = sorted(inbox_path.glob("heartbeat_*.json")) if inbox_path.exists() else []
    pending_trig = sorted(inbox_path.glob("trigger_*.json")) if inbox_path.exists() else []
    total_pending = len(pending_msgs) + len(pending_hb) + len(pending_trig)

    if total_pending:
        parts = []
        if pending_msgs:  parts.append(f"{len(pending_msgs)} msg")
        if pending_hb:    parts.append(f"{len(pending_hb)} heartbeat")
        if pending_trig:  parts.append(f"{len(pending_trig)} trigger")
        oldest = min(
            (p.stat().st_mtime for p in pending_msgs + pending_hb + pending_trig),
            default=None
        )
        row(WARN, "Inbox", ", ".join(parts), f"oldest {_ago(oldest)}")
        for p in (pending_msgs + pending_hb + pending_trig)[:3]:
            try:
                d = json.loads(p.read_text())
                label = d.get("text") or d.get("type") or p.name
                row("  ", f"  {p.name[:24]}", "", str(label)[:40])
            except Exception:
                row("  ", f"  {p.name[:24]}", "", "")
    else:
        last_end = shared.get("claw_last_session_end")
        row(OK, "Inbox", "empty", f"last cleared {_ago(last_end)}" if last_end else "")

    # Session
    lockfile = ghost_home / "ghost_run_dir" / "workflows" / "claw" / ".claude.pid"
    session_log = ghost_home / "ghost_run_dir" / "workflows" / "claw" / "session-launcher.log"
    sessions_dir = ghost_home / "agents" / "claw" / "sessions"

    active_pid: int | None = None
    if lockfile.exists():
        try:
            pid = int(lockfile.read_text().strip())
            if _pid_alive(pid):
                active_pid = pid
            else:
                lockfile.unlink(missing_ok=True)
        except Exception:
            pass

    if active_pid:
        # Try to find current session JSONL
        try:
            latest_jsonl = max(sessions_dir.rglob("session_*.jsonl"), key=lambda p: p.stat().st_mtime)
            age_s = time.time() - latest_jsonl.stat().st_mtime
            activity = f"active {_ago(latest_jsonl.stat().st_mtime)}" if age_s < 15 else f"idle {_ago(latest_jsonl.stat().st_mtime)}"
        except Exception:
            activity = "running"
        row(OK, "Claude session", f"pid {active_pid}", activity)
    else:
        # Last session end
        last_end = shared.get("claw_last_session_end")
        if last_end:
            row(WAIT, "Claude session", "idle", f"last ended {_ago(last_end)}")
        else:
            row(WAIT, "Claude session", "idle", "no sessions yet")

    # ── 5. Recent messages ────────────────────────────────────────────────────
    if db_ok and db_path.exists():
        h("Recent Messages")
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Get last 5 user messages
            if bot_user_id:
                cur.execute("""
                    SELECT update_id, topic_id, text, timestamp, user_name FROM events
                    WHERE event_type='message' AND user_id != ? AND text != ''
                    ORDER BY update_id DESC LIMIT 5
                """, (bot_user_id,))
            else:
                cur.execute("""
                    SELECT update_id, topic_id, text, timestamp, user_name FROM events
                    WHERE event_type='message' AND text != ''
                    ORDER BY update_id DESC LIMIT 5
                """)
            msgs = list(reversed(cur.fetchall()))

            for m in msgs:
                uid = m["update_id"]
                topic_name = topics_map.get(m["topic_id"]) or ("General" if m["topic_id"] is None else f"tid={m['topic_id']}")
                text = (m["text"] or "")[:35]
                ts_str = datetime.fromtimestamp(m["timestamp"]).strftime("%H:%M")

                # Check inbox for this message
                inbox_file = inbox_path / f"msg_{uid}.json"
                in_inbox = inbox_file.exists()

                # Check if bot replied after this (next bot message in same topic)
                cur.execute("""
                    SELECT update_id FROM events
                    WHERE topic_id=? AND event_type='message'
                    AND user_id=? AND update_id > ?
                    ORDER BY update_id ASC LIMIT 1
                """, (m["topic_id"], bot_user_id or -1, uid))
                reply_row = cur.fetchone()
                replied = reply_row is not None

                # Status indicators
                cur_val = cursors.get(topic_name, 0)
                past_cursor = uid <= cur_val

                stage = ""
                if past_cursor and in_inbox:
                    stage = f"{WARN}inbox{NC}"
                elif past_cursor and replied:
                    stage = f"{GREEN}replied{NC}"
                elif past_cursor:
                    stage = f"{DIM}processed{NC}"
                else:
                    stage = f"{YELLOW}pending{NC}"

                row("  ", f"[{ts_str}] {text!r:.34}", f"@{topic_name}", stage)

            conn.close()
        except Exception as e:
            row(WARN, "Recent messages", "error", str(e)[:40])

    return lines


def draw(ghost_home: Path) -> tuple[str, bool]:
    try:
        lines = build_status(ghost_home)
        out = "\n".join(lines) + "\n"
        return out, True
    except Exception as e:
        return f"  {FAIL}  Status error: {e}\n", False


def main():
    parser = argparse.ArgumentParser(description="Ghost + Claw status monitor")
    parser.add_argument("--home", help="GHOST_HOME path")
    parser.add_argument("--once", action="store_true", help="Print once and exit")
    parser.add_argument("--interval", type=float, default=3.0, help="Refresh interval seconds")
    args = parser.parse_args()

    if args.home:
        ghost_home = Path(os.path.expanduser(args.home))
    else:
        env_home = os.environ.get("GHOST_HOME")
        ghost_home = Path(env_home) if env_home else _GHOST_HOME_DEFAULT

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n {BOLD}Ghost Status{NC}  {DIM}{ghost_home}{NC}"

    if args.once:
        print(header)
        print(f" {'─' * 52}")
        out, _ = draw(ghost_home)
        print(out)
        sys.exit(0)

    prev_lines = 0
    try:
        while True:
            now_str = datetime.now().strftime("%H:%M:%S")
            header_line = f"\n {BOLD}Ghost Status{NC}  {DIM}{ghost_home}  {now_str}{NC}"
            sep = f" {'─' * 52}"
            out, _ = draw(ghost_home)
            full = header_line + "\n" + sep + "\n" + out + f"\n {DIM}Refreshing every {args.interval:.0f}s — Ctrl+C to exit{NC}\n"

            if prev_lines > 0:
                sys.stdout.write(f"\033[{prev_lines}A\033[J")

            sys.stdout.write(full)
            sys.stdout.flush()
            prev_lines = full.count("\n") + 1

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
