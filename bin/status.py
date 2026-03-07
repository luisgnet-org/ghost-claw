#!/usr/bin/env python3
"""
Ghost + Claw system status monitor.

Shows live health of the entire pipeline:
  services → telegram → inbox → session → replies

Usage:
  bin/status.py                    # print once and exit
  bin/status.py --watch            # ncurses TUI, refresh every 3s
  bin/status.py --home ~/myghost   # explicit GHOST_HOME
"""

from __future__ import annotations

import argparse
import curses
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Self-locate ────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).parent
_GHOST_HOME_DEFAULT = _SCRIPT_DIR.parent.parent.parent  # bin/ → ghost_claw/ → git/ → GHOST_HOME/

# ── ANSI colours (for non-curses output) ─────────────────────────────────────
GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
YELLOW = "\033[1;33m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
NC     = "\033[0m"

_ANSI_ICONS = {"ok": f"{GREEN}✓{NC}", "fail": f"{RED}✗{NC}", "warn": f"{YELLOW}~{NC}",
               "wait": f"{YELLOW}○{NC}", "blank": "  "}
_CURSES_ICONS = {"ok": "✓", "fail": "✗", "warn": "~", "wait": "○", "blank": " "}


def _ago(ts: float | None) -> str:
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
    try:
        r = subprocess.run(["launchctl", "list", label],
                           capture_output=True, text=True, timeout=3)
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
        return ("running" if pid else "stopped"), pid, exit_code
    except Exception:
        return "unknown", None, None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ── Status data ──────────────────────────────────────────────────────────────
# Each row: (icon_type, label, value, note)
# icon_type: 'ok' | 'fail' | 'warn' | 'wait' | 'blank' | 'header'

def build_status(ghost_home: Path) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    label_prefix = f"com.ghost.{ghost_home.name}"

    def h(title: str):
        rows.append(("header", title, "", ""))

    def row(icon: str, label: str, value: str = "", note: str = ""):
        rows.append((icon, label, value, note))

    # ── 1. Services ──────────────────────────────────────────────────────────
    h("Services")
    for svc in ("daemon", "mcp-proxy", "claw-session"):
        label = f"{label_prefix}.{svc}"
        state, pid, exit_code = _launchctl_status(label)
        if state == "running":
            row("ok", svc, "running", f"pid {pid}")
        elif state == "stopped":
            note = f"exit {exit_code}" if exit_code else "not running"
            row("fail" if svc != "claw-session" else "wait", svc, "stopped", note)
        else:
            row("warn", svc, "unknown", "launchctl lookup failed")

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
            s = socket.create_connection((host, mcp_port), timeout=0.5)
            s.close()
            mcp_ok = True
            break
        except Exception:
            pass
    row("ok" if mcp_ok else "fail", "MCP server",
        "listening" if mcp_ok else "not reachable", f"port {mcp_port}")

    # ── 2. Daemon ────────────────────────────────────────────────────────────
    h("Daemon")
    state_path = ghost_home / "ghost_run_dir" / "state.json"
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            pass

    claw_last = state.get("last_run", {}).get("claw")
    if claw_last:
        row("ok", "Claw workflow last run", "", _ago(claw_last))
    else:
        row("fail", "Claw workflow", "never run", "check daemon + config.yaml")

    log_path = ghost_home / "ghost_run_dir" / "ghost.log"
    if log_path.exists():
        mtime = log_path.stat().st_mtime
        age_s = time.time() - mtime
        icon = "ok" if age_s < 30 else ("warn" if age_s < 120 else "fail")
        row(icon, "Daemon log activity", "", _ago(mtime))
    else:
        row("fail", "Daemon log", "not found", str(log_path))

    # ── 3. Telegram DB ───────────────────────────────────────────────────────
    h("Telegram")
    db_path = ghost_home / "ghost_run_dir" / "telegram" / "telegram.db"
    db_ok = False
    topics_map: dict[int, str] = {}
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
            cur.execute("""SELECT user_id, user_name, COUNT(*) as cnt FROM events
                WHERE event_type='message' AND text='' GROUP BY user_id ORDER BY cnt DESC LIMIT 1""")
            r = cur.fetchone()
            if r:
                bot_user_id = r["user_id"]
            if bot_user_id:
                cur.execute("""SELECT text, timestamp FROM events
                    WHERE event_type='message' AND user_id != ? AND text != ''
                    ORDER BY update_id DESC LIMIT 1""", (bot_user_id,))
            else:
                cur.execute("""SELECT text, timestamp FROM events
                    WHERE event_type='message' AND text != ''
                    ORDER BY update_id DESC LIMIT 1""")
            r = cur.fetchone()
            if r:
                last_user_msg_ts = r["timestamp"]
                last_user_msg_text = (r["text"] or "")[:40]
            conn.close()
            db_ok = True
        except Exception as e:
            row("fail", "Telegram DB", "error", str(e)[:40])

    if db_ok:
        topic_summary = ", ".join(list(topics_map.values())[:4]) or "none"
        last_msg_note = f'last: "{last_user_msg_text}"  {_ago(last_user_msg_ts)}' if last_user_msg_text else _ago(last_user_msg_ts)
        row("ok", "Telegram DB", f"{total_events} events", last_msg_note)
        row("blank", "Topics", topic_summary, "")
    else:
        row("fail", "Telegram DB", "not found", str(db_path))

    # ── 4. Claw pipeline ─────────────────────────────────────────────────────
    h("Claw Pipeline")
    shared = state.get("shared", {})
    cursors: dict = shared.get("claw_topic_cursors", {}) or {}

    if db_ok and cursors and db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            total_unread = 0
            for topic_name, cursor_uid in cursors.items():
                tid = next((k for k, v in topics_map.items() if v == topic_name), None)
                if tid is None:
                    continue
                if bot_user_id:
                    cur.execute("""SELECT COUNT(*) FROM events WHERE topic_id=? AND event_type='message'
                        AND user_id != ? AND text != '' AND update_id > ?""",
                        (tid, bot_user_id, cursor_uid))
                else:
                    cur.execute("""SELECT COUNT(*) FROM events WHERE topic_id=? AND event_type='message'
                        AND text != '' AND update_id > ?""", (tid, cursor_uid))
                total_unread += cur.fetchone()[0]
            conn.close()
            if total_unread:
                row("warn", "Cursors", f"{total_unread} unread", f"{len(cursors)} topic(s)")
            else:
                row("ok", "Cursors", "up to date", f"{len(cursors)} topic(s)")
        except Exception as e:
            row("warn", "Cursor check", "error", str(e)[:40])
    elif not cursors:
        row("wait", "Cursors", "not initialized", "claw hasn't run yet")

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
        oldest = min((p.stat().st_mtime for p in pending_msgs + pending_hb + pending_trig), default=None)
        row("warn", "Inbox", ", ".join(parts), f"oldest {_ago(oldest)}")
    else:
        last_end = shared.get("claw_last_session_end")
        row("ok", "Inbox", "empty", f"last cleared {_ago(last_end)}" if last_end else "")

    lockfile = ghost_home / "ghost_run_dir" / "workflows" / "claw" / ".claude.pid"
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
        try:
            latest_jsonl = max(sessions_dir.rglob("session_*.jsonl"), key=lambda p: p.stat().st_mtime)
            age_s = time.time() - latest_jsonl.stat().st_mtime
            activity = f"active {_ago(latest_jsonl.stat().st_mtime)}" if age_s < 15 else f"idle {_ago(latest_jsonl.stat().st_mtime)}"
        except Exception:
            activity = "running"
        row("ok", "Claude session", f"pid {active_pid}", activity)
    else:
        last_end = shared.get("claw_last_session_end")
        if last_end:
            row("wait", "Claude session", "idle", f"last ended {_ago(last_end)}")
        else:
            row("wait", "Claude session", "idle", "no sessions yet")

    # ── 5. Recent messages ───────────────────────────────────────────────────
    if db_ok and db_path.exists():
        h("Recent Messages")
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if bot_user_id:
                cur.execute("""SELECT update_id, topic_id, text, timestamp, user_name FROM events
                    WHERE event_type='message' AND user_id != ? AND text != ''
                    ORDER BY update_id DESC LIMIT 5""", (bot_user_id,))
            else:
                cur.execute("""SELECT update_id, topic_id, text, timestamp, user_name FROM events
                    WHERE event_type='message' AND text != ''
                    ORDER BY update_id DESC LIMIT 5""")
            msgs = list(reversed(cur.fetchall()))
            for m in msgs:
                uid = m["update_id"]
                topic_name = topics_map.get(m["topic_id"]) or ("General" if m["topic_id"] is None else f"tid={m['topic_id']}")
                text = (m["text"] or "")[:35]
                ts_str = datetime.fromtimestamp(m["timestamp"]).strftime("%H:%M")
                inbox_file = inbox_path / f"msg_{uid}.json"
                in_inbox = inbox_file.exists()
                cur.execute("""SELECT update_id FROM events
                    WHERE topic_id=? AND event_type='message' AND user_id=? AND update_id > ?
                    ORDER BY update_id ASC LIMIT 1""", (m["topic_id"], bot_user_id or -1, uid))
                replied = cur.fetchone() is not None
                cur_val = cursors.get(topic_name, 0)
                past_cursor = uid <= cur_val
                if past_cursor and in_inbox:
                    stage, stage_icon = "inbox", "warn"
                elif past_cursor and replied:
                    stage, stage_icon = "replied", "ok"
                elif past_cursor:
                    stage, stage_icon = "processed", "blank"
                else:
                    stage, stage_icon = "pending", "warn"
                row(stage_icon, f"[{ts_str}] {text!r:.34}", f"@{topic_name}", stage)
            conn.close()
        except Exception as e:
            row("warn", "Recent messages", "error", str(e)[:40])

    return rows


# ── ANSI rendering (default: print once) ─────────────────────────────────────

def render_ansi(rows: list[tuple[str, str, str, str]], ghost_home: Path) -> str:
    lines = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"\n {BOLD}Ghost Status{NC}  {DIM}{ghost_home}  {now_str}{NC}")
    lines.append(f" {'─' * 52}")
    for icon_type, label, value, note in rows:
        if icon_type == "header":
            lines.append(f"\n {BOLD}{label.upper()}{NC}")
        else:
            icon = _ANSI_ICONS.get(icon_type, "  ")
            note_col = f"{DIM}{note}{NC}" if note else ""
            lines.append(f"  {icon}  {label:<28}{value:<18}{note_col}")
    lines.append("")
    return "\n".join(lines)


# ── Curses rendering (--watch) ───────────────────────────────────────────────

def watch_curses(stdscr, ghost_home: Path, interval: float):
    curses.curs_set(0)
    stdscr.timeout(int(interval * 1000))
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)

    _cp = {"ok": 1, "fail": 2, "warn": 3, "wait": 3, "header": 4}

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        y = 0
        now_str = datetime.now().strftime("%H:%M:%S")

        if y < max_y:
            stdscr.addnstr(y, 1, "Ghost Status", max_x - 2, curses.A_BOLD)
            stdscr.addnstr(y, 14, f"  {ghost_home}  {now_str}", max(0, max_x - 15), curses.A_DIM)
            y += 1
        if y < max_y:
            stdscr.addnstr(y, 1, "─" * min(52, max_x - 2), max_x - 2)
            y += 1

        try:
            rows = build_status(ghost_home)
        except Exception as e:
            if y < max_y:
                stdscr.addnstr(y, 1, f"Error: {e}", max_x - 2, curses.color_pair(2))
            stdscr.refresh()
            if stdscr.getch() in (ord('q'), ord('Q'), 27):
                break
            continue

        for icon_type, label, value, note in rows:
            if y >= max_y - 2:
                break
            if icon_type == "header":
                if y > 2:
                    y += 1  # single blank line between sections only (not before first)
                if y >= max_y - 1:
                    break
                stdscr.addnstr(y, 1, label.upper(), max_x - 2, curses.A_BOLD | curses.color_pair(4))
                y += 1
            else:
                cp = curses.color_pair(_cp.get(icon_type, 0))
                icon = _CURSES_ICONS.get(icon_type, " ")
                stdscr.addnstr(y, 2, icon, 2, cp)
                stdscr.addnstr(y, 5, label[:28], 28)
                if value:
                    stdscr.addnstr(y, 34, value[:18], 18)
                if note and max_x > 54:
                    stdscr.addnstr(y, 53, note[:max_x - 54], max_x - 54, curses.A_DIM)
                y += 1

        if y < max_y - 1:
            y += 1
            stdscr.addnstr(y, 1, f"Refreshing every {interval:.0f}s — q to exit", max_x - 2, curses.A_DIM)

        stdscr.refresh()
        if stdscr.getch() in (ord('q'), ord('Q'), 27):
            break


def main():
    parser = argparse.ArgumentParser(description="Ghost + Claw status monitor")
    parser.add_argument("--home", help="GHOST_HOME path")
    parser.add_argument("--watch", action="store_true", help="Live ncurses TUI mode")
    parser.add_argument("--once", action="store_true", help="(deprecated, now the default)")
    parser.add_argument("--interval", type=float, default=3.0, help="Refresh interval for --watch")
    args = parser.parse_args()

    if args.home:
        ghost_home = Path(os.path.expanduser(args.home))
    else:
        env_home = os.environ.get("GHOST_HOME")
        ghost_home = Path(env_home) if env_home else _GHOST_HOME_DEFAULT

    if args.watch:
        curses.wrapper(watch_curses, ghost_home, args.interval)
    else:
        rows = build_status(ghost_home)
        print(render_ansi(rows, ghost_home))


if __name__ == "__main__":
    main()
