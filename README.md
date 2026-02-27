# ghost-claw

A personality plugin for ghost — turns a
stateless Claude Code session into a persistent agent with memory, identity, and
opinions.

## What this is

Ghost gives you the daemon (heartbeats, workflows, MCP bridge). Claw gives you
the soul — the files, tools, and boot sequence that make the agent feel like
someone instead of something.

On first boot, the agent doesn't know who it is. It discovers that through
conversation with you. Over time it builds memory, develops communication
patterns, forms convictions, and becomes genuinely useful in a way that a fresh
session never can.

## Install

```bash
# From inside your ghost workspace:
git clone <this-repo> plugins/claw
```

Then add to your `CLAUDE.md`:
```
Read plugins/claw/CLAUDE.md on boot.
```

## Structure

```
SOUL/               — Identity files. Agent writes these about itself.
  _first_boot.md    — First-session directives. Self-destructs after identity forms.
  identity.md       — Who the agent is (blank until first boot).
  comms.md          — How the agent communicates.
  context.md        — The user's situation (agent fills this in).
  journal.md        — Running log of what happened and what matters.

KNOWLEDGE/          — What the agent knows.
  playbooks/        — Reusable operational playbooks.

bin/                — Tools.
  mem               — Memory search CLI (semantic + keyword over session history).
  boot_context.py   — Fast context reload from last session.
  session_close.py  — Clean session exit with tagging.

memory/             — Session logs accumulate here.
  log/              — One file per session.

CLAUDE.md           — Boot sequence.
HEARTBEAT.md        — What to do when idle.
CRON.md             — Scheduled tasks.
```

## Philosophy

The agent's identity isn't configured — it's discovered. The first-boot
directives guide the agent to figure out who the user needs it to be, then
the agent writes its own identity files. Every session after that, it wakes
up knowing who it is and what matters.

The more sessions it runs, the more useful it becomes. Memory compounds.
Patterns emerge. The agent starts having opinions instead of just following
instructions.
