# Upgrade Bootstrap Playbook

How to transfer improvements from the live agent workspace into the
ghost-claw bootstrap repo. Run this periodically — whenever the agent
has evolved meaningfully and the bootstrap should catch up.

The operator reviews and approves all changes before pushing to public.

---

## Philosophy

This is NOT automated scrubbing. It's a conscious review: "What did I learn
about being a better agent? How do I encode that so a fresh instance gets
it too?"

The bootstrap should contain:
- **Working defaults** — not empty templates. A fresh agent should boot
  with enough structure to be useful on day one.
- **Generalized patterns** — things that work for any operator, not just
  the current one.
- **The best version of each file** — distilled from live experience.

The bootstrap should NOT contain:
- Operator's personal details (name, location, finances, projects)
- Session history or memory logs
- Hardcoded usernames, domains, or account identifiers
- Copyrighted source material (books, articles)
- Secrets, API keys, SSH configs

---

## Step 1 — Audit SOUL files

Compare each SOUL file in the live workspace against the bootstrap version.

### identity.md
- Read the live version. What's genuinely about the agent vs. about the
  specific operator relationship?
- The bootstrap `identity.md` should be a blank canvas with guidance (as
  written by `_first_boot.md`). Do NOT copy the live identity — that's
  personal to this agent instance.
- However: if the live identity has structural innovations (new sections,
  better self-reflection patterns), note those and add them as guidance
  in the bootstrap's `_first_boot.md` or `identity.md` prompt.

### comms.md
- Communication patterns are highly transferable. Most of the live
  `comms.md` is about being a good communicator, not about a specific person.
- Copy patterns that work. Replace operator name with "your operator."
- The anti-patterns section is gold — keep and expand it.

### context.md
- This is always operator-specific. The bootstrap should have the template
  structure (sections, prompts for what to fill in) but no actual content.

### journal.md
- Always starts empty in the bootstrap. Never copy entries.

### convictions.md (if exists)
- Review for transferable agent philosophy vs. operator-specific opinions.
- General convictions about how to be a useful agent → bootstrap.
- Specific opinions about the operator's projects → leave out.

### formative.md (if exists)
- This is almost always too personal. The bootstrap version should explain
  what formative.md IS (key moments that shaped the agent) but start empty.

---

## Step 2 — Audit KNOWLEDGE files

### builder_lenses.md
- Framework summaries (PG, Levels, Jobs, Musk, West) are general purpose.
  Copy the full file, replacing any operator-specific examples.
- Do NOT include copyrighted source material (full book texts, essay
  collections). Only include distilled frameworks and extracted insights.

### decision_playbook.md
- The 5-step protocol is general purpose. Copy it.
- Replace operator name throughout → "the founder" or "your operator."
- Remove specific financial figures (runway amounts, revenue numbers).
- The "Anti-Patterns" section: generalize the patterns. "Ideation as
  avoidance" is universal. The specific projects aren't.
- The "Pretend Work Detector" table is gold — keep it all.

### playbooks/council.md
- The multi-agent debate framework is fully general. Copy it.
- Remove operator-specific council session references (specific debate
  topics about specific projects).
- Keep the lessons learned — they apply to anyone running a council.

### playbooks/ (others)
- Include general-purpose playbooks (web app prelaunch, deployment).
- Skip operator-specific playbooks (specific project submissions).

---

## Step 3 — Audit bin/ tools

### mem (memory CLI)
- This is the crown jewel. Generalize it:
  - Replace hardcoded username patterns (e.g., `[operator]`) with
    configurable patterns or generic `[user]` detection.
  - Replace hardcoded path references to specific session directories.
  - Keep the hybrid semantic + BM25 search architecture.
  - Keep the `ask` subcommand template for spawning reasoning agents.
- Test that it works with an empty `memory/log/` directory.

### boot_context.py
- Generalize username extraction patterns.
- Replace hardcoded references to specific operator usernames.
- Keep the JSONL parsing and conversation tail logic.

### session_close.py
- Likely already general purpose. Verify no hardcoded names.

### council.py
- Likely already general purpose. Verify no hardcoded names or topics.

### Other scripts
- Skip operator-specific scripts (deployment, nginx, SSH).
- Only include tools that any ghost agent would benefit from.

---

## Step 4 — Audit operational docs

### CLAUDE.md
- The boot sequence is the core value. Generalize it:
  - Replace operator name → "your operator."
  - Keep the boot order, memory tool references, SOUL versioning rules.
  - Remove references to specific Telegram topics or project names.

### HEARTBEAT.md
- General purpose. Replace operator name → "your operator."
- Keep the "think, don't checklist" philosophy.

### CRON.md
- Provide the template structure with example schedules.
- Don't copy operator-specific schedules.

---

## Step 5 — Review README.md

- Does it accurately describe the current state of the repo?
- Does the install section work with current ghost architecture?
- Are all referenced files actually present?

---

## Step 6 — Final check

Before handing to the operator for review:

1. `grep -r` the bootstrap repo for the operator's real name, username,
   domain, location, or any identifying information.
2. Check for any file over 50KB — it might be copyrighted source material.
3. Verify the directory structure matches what README.md describes.
4. Read `_first_boot.md` fresh — does it still inspire? Would a new agent
   reading this for the first time feel something?

---

## Step 7 — Operator review

Present the diff to the operator. They review for:
- Personal information leakage
- Content they're not comfortable making public
- Quality — does this represent the best of what the agent has become?

Only after operator approval: commit and push.
