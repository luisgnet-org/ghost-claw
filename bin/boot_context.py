#!/usr/bin/env python3
"""Boot context: read the tail of the last session so the agent wakes up knowing where it was.

Usage:
    python3 bin/boot_context.py [--messages N] [--current-session ID]

Finds the most recent JSONL session file, extracts the last N message
exchanges (default 15), and prints them. This is the FIRST thing to
read on boot — gives enough context to continue or decide to dig deeper.

Works even when sessions crash (no dependency on session_close writing anything).

Configuration (environment variables):
    GHOST_OPERATOR_USERNAME  Telegram username identifying operator messages.
                             Default: auto-detect from first matching message.
    GHOST_SESSIONS_DIR       Override for sessions directory.
                             Default: auto-detect from ~/.claude/projects/
    GHOST_WORKSPACE          Workspace root.
                             Default: parent of bin/ (i.e., this script's parent)
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG — all overridable via environment variables
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("GHOST_WORKSPACE", str(_SCRIPT_DIR.parent)))


def _detect_sessions_dir():
    override = os.environ.get("GHOST_SESSIONS_DIR", "")
    if override:
        return Path(override)

    candidate_homes = []

    real_home = Path.home()
    candidate_homes.append(real_home / ".claude" / "projects")

    sandbox_home = WORKSPACE.parent / "home"
    if sandbox_home.exists():
        candidate_homes.append(sandbox_home / ".claude" / "projects")

    candidate_homes.append(WORKSPACE.parent / ".claude" / "projects")

    slug = str(WORKSPACE).replace("/", "-").lstrip("-")

    for base in candidate_homes:
        candidate = base / slug
        if candidate.exists():
            return candidate

    for base in candidate_homes:
        if base.exists():
            children = [c for c in base.iterdir() if c.is_dir()]
            if children:
                def latest_mtime(d):
                    files = list(d.glob("*.jsonl"))
                    return max((f.stat().st_mtime for f in files), default=0)
                children.sort(key=latest_mtime, reverse=True)
                return children[0]

    return None


SESSIONS_DIR = _detect_sessions_dir()
HANDOFF_PATH = WORKSPACE / "HANDOFF.md"
PT = timezone(timedelta(hours=-8))

OPERATOR_USERNAME = os.environ.get("GHOST_OPERATOR_USERNAME", "").strip()

# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

def find_sessions(exclude_id=None):
    """Find JSONL session files, newest first."""
    sessions = []
    if not SESSIONS_DIR or not SESSIONS_DIR.exists():
        return sessions
    for p in SESSIONS_DIR.glob("*.jsonl"):
        if exclude_id and exclude_id in p.stem:
            continue
        sessions.append((p, p.stat().st_mtime))
    sessions.sort(key=lambda x: x[1], reverse=True)
    return sessions


# ---------------------------------------------------------------------------
# Message extraction
# ---------------------------------------------------------------------------

def extract_tail(jsonl_path, max_exchanges=15):
    """Extract the last N human/assistant exchanges from a session."""
    messages = []
    last_topic = None
    detected_username = OPERATOR_USERNAME

    with open(jsonl_path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type")

            if entry_type in ("human", "user"):
                msg = entry.get("message", {})
                content = msg.get("content", "")
                texts = _extract_texts(content)

                for text in texts:
                    topic = _extract_topic(text)
                    if topic:
                        last_topic = topic

                    if "<system-reminder>" in text[:30]:
                        continue

                    if not detected_username:
                        detected_username = _detect_operator_username(text)

                    op_msg = _extract_operator_message(text, detected_username)
                    if op_msg:
                        messages.append({
                            "role": "operator",
                            "text": op_msg,
                            "topic": last_topic,
                        })

            elif entry_type == "assistant":
                msg = entry.get("message", {})
                content = msg.get("content", "")

                telegram_texts = _extract_telegram_sends(content)
                if telegram_texts:
                    for tg_text in telegram_texts:
                        messages.append({
                            "role": "agent",
                            "text": tg_text[:1200] + (
                                "..." if len(tg_text) > 1200 else ""
                            ),
                            "telegram": True,
                        })

    # Deduplicate consecutive messages with identical text
    deduped = []
    seen_texts = set()
    for m in messages:
        key = m["text"][:200]
        if key in seen_texts:
            continue
        seen_texts.add(key)
        deduped.append(m)
    messages = deduped

    op_msgs = [m for m in messages if m["role"] == "operator"]
    agent_msgs = [m for m in messages if m["role"] == "agent"]

    agent_budget = max(0, max_exchanges - len(op_msgs))
    agent_tail = agent_msgs[-agent_budget:] if agent_budget else []

    op_set = set(id(m) for m in op_msgs)
    agent_set = set(id(m) for m in agent_tail)
    tail = [m for m in messages if id(m) in op_set or id(m) in agent_set]

    return tail, last_topic


def _detect_operator_username(text):
    """Auto-detect the operator username from hook injection text."""
    match = re.search(r'\[([a-zA-Z0-9_]+)\]\s+\S', text)
    if match:
        candidate = match.group(1)
        if candidate.lower() not in ("system", "tool", "assistant", "user"):
            return candidate

    match = re.search(r'NEW MESSAGE FROM ([a-zA-Z0-9_]+)[^:]*:', text)
    if match:
        return match.group(1)

    return ""


def _extract_operator_message(text, username):
    """Extract the operator's actual message from hook injection or user prompt."""
    if username:
        pattern = r'\[' + re.escape(username) + r'\]\s*(.+?)(?=\nRespond to these|\Z)'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

        pattern2 = (
            r'NEW MESSAGE FROM ' + re.escape(username)
            + r'[^:]*:\s*(.+?)(?=\\n\\n\s*$|\Z)'
        )
        match = re.search(pattern2, text, re.DOTALL)
        if match:
            return match.group(1).strip().replace('\\n', '\n')

    else:
        match = re.search(
            r'\[([a-zA-Z0-9_]+)\]\s*(.+?)(?=\nRespond to these|\Z)',
            text, re.DOTALL
        )
        if match:
            candidate = match.group(1)
            if candidate.lower() not in ("system", "tool", "assistant", "user"):
                return match.group(2).strip()

    return None


def _extract_telegram_sends(content):
    """Extract text from Telegram send_message tool calls."""
    texts = []
    if not isinstance(content, list):
        return texts
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        if "send_message" not in name:
            continue
        inp = block.get("input", {})
        text = inp.get("text", "").strip()
        if text:
            texts.append(text)
    return texts


def _extract_texts(content):
    """Extract text strings from message content (handles both formats)."""
    texts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block["text"].strip()
                if not text:
                    continue
                if "<system-reminder>" in text[:30]:
                    if "topic=" in text:
                        texts.append(text)
                else:
                    texts.append(text)
    elif isinstance(content, str) and content.strip():
        texts.append(content.strip())
    return texts


def _extract_topic(text):
    """Extract topic from hook injection or system-reminder."""
    match = re.search(r'topic=([A-Z][A-Z ]*?)[\s(,\\]', text)
    if match:
        return match.group(1).strip()
    match = re.search(r'topic=([A-Z][A-Z ]+)', text)
    if match:
        return match.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Journal context
# ---------------------------------------------------------------------------

def _get_latest_journal_entry():
    """Extract the most recent journal entry (first ## section)."""
    journal = WORKSPACE / "memory" / "journal.md"
    if not journal.exists():
        return None
    try:
        text = journal.read_text()
    except OSError:
        return None

    sections = re.split(r'^## ', text, flags=re.MULTILINE)
    if len(sections) < 2:
        return None

    first_section = "## " + sections[1]
    if len(first_section) > 1000:
        first_section = first_section[:1000] + "..."
    return first_section.strip()


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_output(session_path, tail, topic, age_minutes, journal_summary=None):
    """Format the boot context output."""
    lines = []

    if age_minutes < 5:
        freshness = "just now"
    elif age_minutes < 60:
        freshness = f"{age_minutes:.0f} min ago"
    elif age_minutes < 1440:
        freshness = f"{age_minutes / 60:.1f} hours ago"
    else:
        freshness = f"{age_minutes / 1440:.1f} days ago"

    lines.append(f"## Boot Context (last session ended {freshness})")
    lines.append(f"Session: `{session_path.stem}`")
    if topic:
        lines.append(f"Topic: {topic}")
    lines.append("")

    if not tail:
        lines.append("(No messages extracted)")
    else:
        for msg in tail:
            if msg["role"] == "operator":
                lines.append(f"**OPERATOR:** {msg['text']}")
            else:
                lines.append(f"**AGENT:** {msg['text']}")
            lines.append("")

    if journal_summary:
        lines.append("### Latest Journal Entry")
        lines.append(journal_summary)
        lines.append("")

    lines.append("---")
    if age_minutes < 10:
        lines.append("Recency: VERY RECENT — continue the conversation directly.")
    elif age_minutes < 60:
        lines.append(
            "Recency: RECENT — you have good context. "
            "Check if anything was left unfinished."
        )
    elif age_minutes < 360:
        lines.append(
            "Recency: A FEW HOURS — skim journal.md for anything "
            "that happened between sessions."
        )
    else:
        lines.append(
            "Recency: STALE — do a full boot. Read journal.md "
            "and session_index.md."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    max_messages = 15
    current_session = None

    args = sys.argv[1:]
    if "--messages" in args:
        idx = args.index("--messages")
        if idx + 1 < len(args):
            max_messages = int(args[idx + 1])

    if "--current-session" in args:
        idx = args.index("--current-session")
        if idx + 1 < len(args):
            current_session = args[idx + 1]

    if not SESSIONS_DIR:
        print("No sessions directory found. Set GHOST_SESSIONS_DIR to override.")
        return

    sessions = find_sessions(exclude_id=current_session)
    if not sessions:
        print(f"No prior sessions found in {SESSIONS_DIR}")
        return

    session_path, mtime = sessions[0]
    age_minutes = (datetime.now().timestamp() - mtime) / 60

    tail, topic = extract_tail(session_path, max_exchanges=max_messages)

    journal_summary = _get_latest_journal_entry()

    output = format_output(session_path, tail, topic, age_minutes,
                           journal_summary=journal_summary)
    print(output)

    try:
        HANDOFF_PATH.write_text(output + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
