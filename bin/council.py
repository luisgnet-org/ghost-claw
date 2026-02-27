#!/usr/bin/env python3
"""Council chat — inter-agent communication for multi-agent debates.

Reads agent identity and coordination params from environment.
Targets a configurable Telegram topic (default: COUNCIL).

Usage:
    council.py --start "topic question" --agents "Muse1 🔮, Muse2 🚀, Muse3 📊"
    council.py --write "message text"
    council.py --read [--wait SECONDS] [--after LINE]
    council.py --end [--summary "optional summary"]
    council.py --clear

Environment:
    AGENT_NAME    — display name (e.g. "Muse1")
    AGENT_EMOJI   — emoji suffix (e.g. "🚀")
    COUNCIL_SIZE  — total number of agents in council (e.g. 4)
    AGENT_IDX     — this agent's turn index, 0-based (e.g. 2)
    COUNCIL_TOPIC — Telegram topic name (default: "COUNCIL")
    MCP_PORT      — ghost daemon MCP port (default: 7865)

Round-robin coordination:
    When COUNCIL_SIZE and AGENT_IDX are set, --read --wait blocks until
    it's this agent's turn: total_messages % COUNCIL_SIZE == AGENT_IDX.
    A per-turn timeout prevents deadlocks if an agent crashes.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
CHAT_FILE = WORKSPACE / ".council" / "chat.jsonl"
TOPIC = os.environ.get("COUNCIL_TOPIC", "COUNCIL")
MCP_PORT = os.environ.get("MCP_PORT", "7865")
MCP_URL = f"http://localhost:{MCP_PORT}/mcp"

# How long to wait for a single turn before giving up (deadlock prevention)
TURN_TIMEOUT = 120


def _agent_name(override=None):
    name = override or os.environ.get("AGENT_NAME")
    if not name:
        print("error: AGENT_NAME env var or --agent-name not set", file=sys.stderr)
        sys.exit(1)
    return name


def _agent_emoji(override=None):
    return override or os.environ.get("AGENT_EMOJI", "")


def _council_size():
    val = os.environ.get("COUNCIL_SIZE")
    return int(val) if val else None


def _agent_idx():
    val = os.environ.get("AGENT_IDX")
    return int(val) if val else None


# --- MCP helpers ---

def _post(payload, session_id=None):
    data = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(MCP_URL, data=data, headers=headers)
    resp = urllib.request.urlopen(req, timeout=15)
    return resp.read().decode(), resp.headers.get("Mcp-Session-Id")


def _init_session():
    body, sid = _post({
        "jsonrpc": "2.0", "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "council", "version": "0.1"},
        },
    })
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"},
          session_id=sid)
    return sid


def _call_tool(name, arguments):
    sid = _init_session()
    body, _ = _post({
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }, session_id=sid)
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return {"raw": body}


def _send_telegram(text):
    """Send formatted message to council topic."""
    try:
        return _call_tool("send_message", {"text": text, "topic": TOPIC})
    except Exception as e:
        print(f"warning: telegram send failed: {e}", file=sys.stderr)
        return None


# --- Chat file operations ---

def _ensure_chat_dir():
    CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)


def _append(entry):
    _ensure_chat_dir()
    with open(CHAT_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _read_lines(after=0):
    """Read chat lines. If after > 0, skip that many lines."""
    if not CHAT_FILE.exists():
        return []
    with open(CHAT_FILE) as f:
        lines = f.readlines()
    entries = []
    for i, line in enumerate(lines):
        if i < after:
            continue
        try:
            entries.append((i, json.loads(line)))
        except json.JSONDecodeError:
            continue
    return entries


def _line_count():
    if not CHAT_FILE.exists():
        return 0
    with open(CHAT_FILE) as f:
        return sum(1 for _ in f)


def _format_entry(entry):
    """Format a chat entry for display."""
    name = entry.get("agent", "?")
    emoji = entry.get("emoji", "")
    text = entry.get("text", "")
    header = f"`{name}` {emoji}".strip()
    return f"{header}\n{text}"


def _is_my_turn(count):
    """Check if it's this agent's turn based on round-robin."""
    size = _council_size()
    idx = _agent_idx()
    if size is None or idx is None:
        return True  # No round-robin configured, always allowed
    return count % size == idx


# --- Commands ---

def cmd_start(topic, agents=None):
    """Send a visual start marker to Telegram and reset local chat."""
    _ensure_chat_dir()
    if CHAT_FILE.exists():
        CHAT_FILE.unlink()

    roster = ""
    if agents:
        roster = "\n" + "  ".join(agents.split(","))

    tg_text = (
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "\U0001f3db COUNCIL START\n"
        f"\n{topic}\n"
        f"{roster}\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
    )
    _send_telegram(tg_text)

    entry = {
        "agent": "_system",
        "emoji": "\U0001f3db",
        "text": topic,
        "ts": time.time(),
        "type": "start",
    }
    _append(entry)
    print(f"council started: {topic}")


def cmd_end(summary=None):
    """Send a visual end marker to Telegram."""
    entries = _read_lines()
    agent_msgs = [e for _, e in entries if e.get("agent") != "_system"]

    parts = [
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501",
        "\U0001f3db COUNCIL END",
        f"\n{len(agent_msgs)} messages from {len(set(e['agent'] for e in agent_msgs))} agents",
    ]
    if summary:
        parts.append(f"\n\U0001f4cb {summary}")
    parts.append("\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")

    tg_text = "\n".join(parts)
    _send_telegram(tg_text)

    entry = {
        "agent": "_system",
        "emoji": "\U0001f3db",
        "text": summary or "council ended",
        "ts": time.time(),
        "type": "end",
    }
    _append(entry)
    print(f"council ended ({len(agent_msgs)} messages)")


def cmd_write(text, agent_name=None, agent_emoji=None):
    name = _agent_name(agent_name)
    emoji = _agent_emoji(agent_emoji)

    entry = {
        "agent": name,
        "emoji": emoji,
        "text": text,
        "ts": time.time(),
    }
    _append(entry)

    tg_text = f"`{name}` {emoji}\n{text}"
    _send_telegram(tg_text)

    line_num = _line_count()
    print(f"sent (line {line_num})")


def cmd_read(wait=0, after=0):
    """Read council messages. Optionally wait for new ones.

    With round-robin (COUNCIL_SIZE + AGENT_IDX set):
    Blocks until total_messages % COUNCIL_SIZE == AGENT_IDX, meaning
    it's this agent's turn to speak.

    Without round-robin:
    Waits for at least one new message, then a short grace period.
    """
    size = _council_size()
    idx = _agent_idx()
    use_round_robin = size is not None and idx is not None

    if wait > 0:
        deadline = time.time() + wait
        turn_start = time.time()

        if use_round_robin:
            while time.time() < deadline:
                count = _line_count()
                if count > after and _is_my_turn(count):
                    break
                if time.time() - turn_start > TURN_TIMEOUT:
                    print(f"warning: turn timeout after {TURN_TIMEOUT}s, "
                          f"proceeding anyway", file=sys.stderr)
                    break
                time.sleep(0.5)
        else:
            seen = after
            grace_deadline = None

            while time.time() < deadline:
                current = _line_count()
                if current > seen:
                    if grace_deadline is None:
                        grace_deadline = time.time() + 3.0
                    elif time.time() > grace_deadline:
                        break
                    seen = current
                elif grace_deadline and time.time() > grace_deadline:
                    break
                time.sleep(0.5)

    entries = _read_lines(after=after)
    if not entries:
        print("(no messages)")
        return

    for i, entry in entries:
        print(f"[{i}] {_format_entry(entry)}")
        print()

    total = _line_count()
    print(f"---\n{total} total messages")


def cmd_clear():
    _ensure_chat_dir()
    if CHAT_FILE.exists():
        CHAT_FILE.unlink()
    print("council chat cleared")


# --- CLI ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Council inter-agent chat")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", metavar="TOPIC", help="Start a council session")
    group.add_argument("--write", metavar="TEXT", nargs="?", const="__FROM_FILE__", help="Send a message (text or use --file)")
    group.add_argument("--read", action="store_true", help="Read messages")
    group.add_argument("--end", action="store_true", help="End the council session")
    group.add_argument("--clear", action="store_true", help="Clear chat (no TG msg)")

    parser.add_argument("--agents", help="Comma-separated roster (with --start)")
    parser.add_argument("--summary", help="Summary text (with --end)")
    parser.add_argument("--wait", type=int, default=0,
                        help="Seconds to wait for new messages (with --read)")
    parser.add_argument("--after", type=int, default=0,
                        help="Only show messages after this line number")
    parser.add_argument("--agent-name", help="Agent display name (alternative to AGENT_NAME env)")
    parser.add_argument("--agent-emoji", help="Agent emoji (alternative to AGENT_EMOJI env)")
    parser.add_argument("--file", help="Read message text from file instead of --write arg")

    args = parser.parse_args()

    if args.start:
        cmd_start(args.start, agents=args.agents)
    elif args.write is not None:
        text = args.write
        if args.file or text == "__FROM_FILE__":
            if not args.file:
                print("error: --write with no text requires --file", file=sys.stderr)
                sys.exit(1)
            with open(args.file) as f:
                text = f.read().strip()
        cmd_write(text, agent_name=args.agent_name, agent_emoji=args.agent_emoji)
    elif args.read:
        cmd_read(wait=args.wait, after=args.after)
    elif args.end:
        cmd_end(summary=args.summary)
    elif args.clear:
        cmd_clear()
