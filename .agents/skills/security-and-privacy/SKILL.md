---
name: security-and-privacy
description: Review the affected application access/data boundary with concrete allow/deny scenarios and evidence rather than a blanket security audit.
version: 1.0.0
---

# Contextual application security

## Inputs

The affected scenario, actual subject/resource/action, ownership and trust/access boundaries, public data contract and relevant failure/abuse cases from target-project documentation and code. Resolve runtime/commands/routing through the selected Poise project; keep its configuration inside Poise. Read current Task requirements and evidence through [poise-workflow](../poise/SKILL.md).

## Procedure

1. Determine which security boundary actually changed. Identify where authority is checked and where data/effects become observable. Apply tenant isolation, roles, CSRF/session semantics, secrets, audit or privacy retention only when the real project context requires them; do not impose an unrelated architecture or checklist on every Task.
2. Check the legitimate allowed path and meaningful denial cases at the server/application boundary. UI visibility alone is not an access guard. Select relevant wrong-owner/subject/action, malformed input, stale authority or cross-boundary cases from the actual contract, not fabricated tenant/role models.
3. Trace sensitive input/output and effects to the owner. Validate before the protected effect at the authoritative boundary. Check logs/errors/public serialization for unintended disclosure where the scenario can expose it. Use independent test-owned values and narrowly controlled test credentials/environment; do not propagate real secrets into fixtures or evidence.
4. Validate the declared public representation against its contract. No mandatory JsonSerializeTrait, JsonSerializable implementation or per-case approval ceremony is introduced. A safe DTO/public boundary is preferable to exposing private fields solely for testing. Select the [test-data policy](../../references/test-data-and-assertion-policy.md) when exact fixtures/oracles are needed.
5. Reproduce concrete issues with the triggering subject/resource/action, source revision, observed effect, expected obligation and targeted evidence. Distinguish runtime defect, missing requirement/context and untested risk. Do not turn a hypothetical possibility into a confirmed vulnerability.
6. Run focused allow/deny/serialization boundary checks plus maintained fast smoke as appropriate. A full security audit, deployment or external penetration test requires its own explicit scope and authorization; this skill does not grant it. Route only the specialists needed by the changed boundary.

## Output

Concrete findings with conditions, violated contract, impact and actual evidence/checks, or a bounded no-finding result stating the inspected scenarios and limitations. Save through existing Task/feedback/evidence owners and public handoff. No-finding within this scope is not a claim that the whole application is secure.
