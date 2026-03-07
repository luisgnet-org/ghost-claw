#!/usr/bin/env python3
"""Write a standardized session index entry at session end.

Usage:
    python3 bin/session_close.py \
        --tags "tag1,tag2,tag3" \
        --state "brief description of what happened" \
        --emotional "optional emotional context"

Automatically resolves:
- Current timestamp
- JSONL session ID (from Claude Code session files)
"""

import argparse
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
INDEX_PATH = WORKSPACE / "memory" / "session_index.md"

# Auto-detect timezone (default PT, adjust for your location)
LOCAL_TZ = timezone(timedelta(hours=-8))


def find_sessions_dir():
    """Find the Claude Code sessions directory for this workspace."""
    # Claude Code stores sessions under ~/.claude/projects/<workspace-hash>/
    home = Path.home()
    claude_projects = home / ".claude" / "projects"
    if not claude_projects.exists():
        return None

    # Look for directories that might contain our session JSONLs
    for d in claude_projects.iterdir():
        if d.is_dir():
            jsonls = list(d.glob("*.jsonl"))
            if jsonls:
                return d
    return None


def get_jsonl_id():
    """Get the current session's JSONL ID (second newest file)."""
    sessions_dir = find_sessions_dir()
    if not sessions_dir:
        return "unknown"

    jsonls = sorted(
        sessions_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if len(jsonls) >= 2:
        return jsonls[1].stem  # second newest = current session
    elif jsonls:
        return jsonls[0].stem
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="Write session index entry")
    parser.add_argument("--tags", required=True, help="Comma-separated tags")
    parser.add_argument("--state", required=True, help="Brief state description")
    parser.add_argument("--emotional", default="", help="Emotional context (optional)")
    args = parser.parse_args()

    now = datetime.now(LOCAL_TZ)
    timestamp = now.strftime("%Y-%m-%d %H:%M")
    jsonl_id = get_jsonl_id()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    tags_str = ", ".join(tags)

    parts = [f"{timestamp} | {jsonl_id} | tags: {tags_str}"]
    if args.emotional:
        parts.append(f"emotional: {args.emotional}")
    parts.append(f"state: {args.state}")

    entry = " | ".join(parts)

    # Append to session index
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "a") as f:
        f.write(f"\n{entry}\n")

    print(f"Index: {entry}")

    # Write HANDOFF.md by running boot_context.py
    boot_script = WORKSPACE / "bin" / "boot_context.py"
    if boot_script.exists():
        try:
            subprocess.run(
                [sys.executable, str(boot_script)],
                cwd=str(WORKSPACE),
                timeout=10,
            )
            print("HANDOFF.md written.")
        except Exception as e:
            print(f"HANDOFF.md write failed: {e}")


if __name__ == "__main__":
    main()
