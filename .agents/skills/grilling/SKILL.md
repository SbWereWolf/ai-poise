---
name: grilling
description: Challenge an explicitly requested plan or decision through bounded, dependency-aware question rounds without silently authorizing implementation.
version: 2.0.0
license: MIT
metadata:
  source: https://github.com/mattpocock/skills/tree/0ab1b63a410a03d3627979a109c8695de27af954/skills/productivity/grilling
---

# Grilling

Keep this as a separate skill, activated only by an **explicit request to challenge decisions**. Do not turn ordinary task execution into an unsolicited interview. Scope the discussion to the agreed subject and completion criteria rather than an endless search for every imaginable concern.

## Inputs

The requested decision/plan, intended outcome, agreed subject and completion criteria, known decisions and their authority, open facts and dependent choices. When operating in Poise, obtain current Task/taskless context and permitted work through [poise-workflow](../poise/SKILL.md). Poise routing/configuration stays inside the selected Poise project; target materials are read-only facts unless a separate write scope authorizes changes.

## Procedure

1. Establish the bounded decision tree using existing context. A node is a real choice whose answer changes a downstream decision, acceptance condition or risk—not a question already answered or an invitation to restate settled requirements. Mark settled, open, researched and deliberately deferred items distinctly.
2. Research open facts through available files/tools and authoritative sources instead of asking the user to find facts the agent can verify. Preserve sources and uncertainty. A genuinely unresolved fact remains a prerequisite only for its dependent choices; it does not stall unrelated ready questions.
3. Form the current frontier: decisions whose prerequisites are settled. Ask independent questions together in **manageable rounds**, grouped by topic with clear choices, trade-offs and a reasoned recommendation when useful. Do not force the entire frontier into an overwhelming message. Ask dependent questions only after their prerequisites are answered; do not guess an answer and immediately branch from it.
4. Wait for the user's answers to the current round. Recompute the frontier and record resulting decisions. Do not repeat settled questions unless new material evidence or an explicit scope change invalidates them; state the reason before reopening. Preserve the user's valid alternatives rather than steering toward a predetermined implementation.
5. Store accepted decisions, rationale, facts, deferred questions with explicit owner/condition and remaining risks in the existing authorized work materials. In a formal Poise Task use current content/evidence/artifact owners; do not create a parallel interview registry or modify managed state directly. A discussion-only request does not authorize repository edits.
6. Finish when the agreed criteria are met and no in-scope decision is silently assumed: each relevant branch is settled or explicitly deferred. An empty ready frontier caused by a missing prerequisite is a blocker, not completion. Summarize what was agreed, what remains open and the exact authority boundary.

## Output

A bounded decision map, accepted rationale and source facts, recorded deferrals/blockers and a clear discussion result in existing work materials or the requested response. Interview completion does not renew previous authorization, approve publication or authorize implementation when only discussion was requested. Loading this skill also grants no delegation authority. Use the existing Poise handoff/verification contract only for the work actually performed; do not claim automatic routing or independent review from an interview.
