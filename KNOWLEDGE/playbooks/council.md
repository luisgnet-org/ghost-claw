# Council Playbook

Multi-agent strategic debate. Two execution modes: live (via `bin/council.py`
+ Telegram) or internal (parallel Task agents, compiled output).

---

## When to Summon

- Big directional decisions ("what should I work on next?")
- Stuck in analysis paralysis, need external pressure
- Stress-testing a thesis before committing
- Quarterly strategy reviews

NOT for: tactical decisions, implementation choices, quick yes/no calls.

---

## The Panel (6-8 agents)

### Muses (3) — propose directions

Each argues from a specific worldview with extracted source material loaded.
Populate from `KNOWLEDGE/lenses.md` — the operator's chosen influences.

| Slot | Role | Source |
|---|---|---|
| Muse 1 | First lens from lenses.md | `sources/<name>_extracted.md` |
| Muse 2 | Second lens | `sources/<name>_extracted.md` |
| Muse 3 | Third lens | `sources/<name>_extracted.md` |

### Antagonists (2-3) — kill bad ideas

Each attacks from a different angle. Default roster:

| Name | Emoji | Lens |
|---|---|---|
| The Executioner | ⚔️ | Cognitive bias detector, kills sunk costs, enforces revenue focus |
| The Operator | 🔧 | Reality constraints — time, energy, sequencing, what's executable |
| The Customer | 👤 | "Why would I pay?" Represents the buyer, not the builder |

Custom antagonists for specific debates:
- **The Regulator** — compliance/legal-heavy decisions
- **The Competitor** — role-plays as the strongest competitor
- **The Investor** — asks the hard fundraising questions
- **The Skeptic** — pure devil's advocate, argues against whatever majority says

### Synthesizer (1)

- Opus model. Reads all rounds. Finds convergence, divergence, blind spots.
- Produces the definitive recommendation with specific dates and actions.

### Models

- Muses and Antagonists: **Sonnet** (fast, cheap, capable argumentation)
- Synthesizer: **Opus** (highest reasoning for finding signal in noise)

---

## The Briefing Document

Every agent reads this. Most important artifact. Needs:

1. **Founder situation** — skills, constraints, runway, energy, day job, location
2. **What's been built** — every project with status and revenue
3. **What's failed/stalled and WHY** — patterns, anti-patterns, honest assessment
4. **Competitive landscape** — if relevant to the question
5. **Distribution channels** — what the founder has access to
6. **The question** — specific. "What should I work on after X?" not "What should I do?"

Keep under 1500 words. Dense, not verbose.

---

## The Debate Format

### Format Options

| Format | Char Limit | Rounds | Best For |
|---|---|---|---|
| Essay | 400-600 words | 2 rounds + synthesis | Deep strategic analysis |
| Rapid-fire | 280 chars | 5 rounds | Dialogue, convergence, idea generation |

**Default: 280-char rapid-fire, 5 rounds.** Produces real dialogue instead of
parallel essays. Agents actually respond to each other.

### Round Structure (Rapid-fire format)

- **R1:** Opening takes. Each agent's core position.
- **R2:** Challenge. If R1 shows groupthink, force agents off-script.
- **R3:** Debate. Attack weakest ideas, defend strongest. Kill and consolidate.
- **R4:** Sharpen. React to emerging insights. Name convergence points.
- **R5:** Final vote. One sentence, one pick, one reason.
- **Synthesis** (optional): Opus reads all rounds, produces final recommendation.

### Round Structure (Essay format)

**Round 1** — Opening Arguments (all agents in parallel). 400-600 words.
Muses propose. Antagonists critique. All end with one "do this week" action.

**Round 2** — Cross-Examination (all agents in parallel). 300-400 words.
Where do they agree? Where are others wrong? Has anything changed?

**Synthesis** — Single Opus agent. 800-1200 words. Produces:
1. **Where the council converged** — genuine agreement
2. **Where they diverged and who was right** — resolve tensions
3. **Blind spots** — what nobody mentioned
4. **THE RECOMMENDATION** — one direction, specific actions, dates
5. **The 4D chess move** — the move that makes everything else easier

---

## Execution Modes

### Mode A: Internal (fast, ~5 min)

Run all agents as parallel Task tool calls. Compile outputs between rounds.
Deliver as PDF to Telegram.

```
Round 1: 6 parallel Task calls (sonnet) → collect outputs
Round 2: 6 parallel Task calls (sonnet, fed R1 outputs) → collect
Synthesis: 1 Task call (opus, fed everything) → PDF → Telegram
```

**Best for:** speed, when you need the answer now.

### Mode B: Live on dedicated Telegram topic (~10 min)

Stream each agent's response to Telegram in real-time. The operator watches
the debate unfold.

```bash
# 1. Start — clears chat, sends bookend to Telegram
python3 bin/council.py --start "your question here" \
    --agents "Muse1 🔮, Muse2 🚀, Muse3 📊"

# 2. Spawn subagents via Task tool
#    Each: reads → thinks → writes → reads → writes
#    Round-robin ensures ordered turns

# 3. End — sends bookend + summary
python3 bin/council.py --end --summary "key takeaway"
```

**Best for:** engagement, when process matters as much as output.

### Environment Variables (Mode B only)

| Variable | Required | Example | Purpose |
|---|---|---|---|
| `AGENT_NAME` | yes | `Muse1` | Display name |
| `AGENT_EMOJI` | no | `🔮` | Emoji prefix |
| `COUNCIL_SIZE` | for round-robin | `3` | Total agents |
| `AGENT_IDX` | for round-robin | `1` | This agent's turn (0-based) |

### Round-Robin Coordination (Mode B)

`--read --wait` blocks until it's this agent's turn:
```
turn = total_messages % COUNCIL_SIZE == AGENT_IDX
```

120s per-turn timeout prevents deadlocks.

---

## Subagent Prompt Template

### Mode A (Task tool)

```
You are [CHARACTER] — [1-line description]. You are on a council advising
a founder.

You are NOT a helpful assistant. You are [CHARACTER]. You have strong opinions.
You don't hedge. You say uncomfortable things.

[Character-specific principles — 5-10 bullets]

---

## THE FOUNDER BRIEFING
[Full briefing document]

---

## YOUR TASK
Write 400-600 words as [CHARACTER]. Be direct, uncomfortable, specific.
[Character-specific instructions]
End with one concrete "do this week" action.
```

### Mode B (council.py)

```
You are {NAME} ({emoji}). {PERSONA_DESCRIPTION}

You are in a council debate. The topic is: "{TOPIC}"

To participate:
1. Run: python3 bin/council.py --read --wait 120 --after 0
2. Read what others said. Think about it.
3. Run: python3 bin/council.py --write "your response"
4. Repeat for {ROUNDS} rounds.

Rules:
- Respond to what others said, don't monologue
- Keep messages under 200 words
- Hold positions — don't hedge
- Disagree directly
```

---

## Storing Output

All sessions saved to `.council/`:
- `chat.jsonl` — structured log (for programmatic access)
- `council_NNN_topic.md` — full transcript in markdown
- PDF sent to Telegram for reading

---

## Cost Estimate

Per full council session (6 agents, 2 rounds + synthesis):
- Round 1: 6 × Sonnet (~30K tokens each) ≈ $0.54
- Round 2: 6 × Sonnet (~30K tokens each) ≈ $0.54
- Synthesis: 1 × Opus (~32K tokens) ≈ $0.96
- **Total: ~$2.04**

---

## Intensity Levels

| Level | Agents | Rounds | Use for |
|---|---|---|---|
| Quick | 3 | 1 round, no synthesis | Smaller decisions |
| Full | 6-8 | 2 rounds + synthesis | Major direction changes |
| Deep | 6-8 | 3 rounds + synthesis | When Round 2 produces new tensions |

---

## Lessons Learned

1. **The briefing document is everything.** Rich context → genuinely different perspectives.
2. **Antagonists are the most valuable agents.** They create the pressure that reveals truth.
3. **Round 2 is where the magic happens.** Without cross-examination, you get 6 monologues.
4. **The Customer reframe changes debates.** A buyer-perspective agent prevents echo chambers.
5. **Question sharpness matters.** Focused question + full context → real recommendation.
6. **Agents miss founder history.** Ensure the briefing includes career history.
7. **280 chars is the sweet spot for rapid-fire.** 140 is too tight.
8. **Split verdicts beat consensus.** A 3-3 split reveals real tension.
