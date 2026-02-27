#!/usr/bin/env python3
"""Fast context reload from the last session.

Reads the most recent JSONL session file and extracts the tail of the
conversation — what the operator said and what the agent replied. Writes
a HANDOFF.md file for the next session to pick up where this one left off.

This is the first thing that runs on boot. Goal: 10 seconds to know
where you left off.

TODO: Port and generalize from live agent. Needs:
- JSONL session file discovery (auto-detect Claude Code project dir)
- Message extraction (operator messages vs agent messages)
- Conversation tail with budget (keep all operator msgs, trim agent msgs)
- HANDOFF.md generation
"""

import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent


def main():
    print("## Boot Context")
    print()
    print("boot_context.py is a stub. Run the upgrade playbook to port")
    print("the full implementation from the live agent.")
    print()
    print(f"Workspace: {WORKSPACE}")


if __name__ == "__main__":
    main()
