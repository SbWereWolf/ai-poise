---
name: harness-recovery
description: Diagnose Poise refusals that block authorized task work, record incidents and manual recovery, and choose a verified continuation without fabricating lifecycle results.
---

# Harness recovery

Apply when Poise prevents authorized task work from continuing through its ordinary API.
A failing subject test needs subject remediation, not a harness workaround.

Canonical policy: [harness failure and manual
recovery](../../../docs/governance/development-rules.md#сбой-harness-и-ручной-обход).
Read that section before choosing a recovery; it owns classification, authority,
logs, rollback, repair drafts and reconciliation. This skill adds no repair API.
Use the [common permanent working store](../../../docs/operations/harness-incident-journal.md#постоянное-хранилище)
and its [record/readback procedure](../../../docs/operations/harness-incident-journal.md#запись-и-проверка).
Keep operational records outside Git; update versioned known-bug summaries separately.
Before an agreed restart, both roles inspect the actual progression/position using
the [canonical preflight](../../../docs/workflows/pilot-task-preflight.md#сверка-продолжения-перед-restart).

1. Read the actual input, full saved response, contract/configuration and effect state.
   Classify agent/task-planner misuse, configuration, code or manual-data damage;
   keep unproved causes explicitly unknown and assess recurrence separately.
2. Use the available public owner first. Choose a proportionate correction or manual
   route within the assignment. Configuration defects remain obligations until corrected;
   reject and safely roll back failed fixes rather than layering contradictory settings.
3. Persist every refusal/incident and manual intervention immediately in that common
   store, with exact changes, authority, alternatives and verification. Sprint/Task
   documents link to permanent bytes, not the only disposable copy. Read back files
   and verify SHA-256 before terminal handling or cleanup. If a native owner is
   unavailable, label native registration absent without postponing permanent storage.
4. For repeated or repeatable problems, check existing drafts and create or augment one.
   A draft can request instruction/configuration/procedure repair; code is not automatic.
   If the API is unavailable, preserve a text draft and the unresolved registration duty.
5. Verify the originally blocked action and affected adjacent scenarios. Distinguish
   recovered subject work, repaired configuration/code, unknown outcomes and native
   lifecycle status. Reconcile through the public owner when available again.

A one-value database edit is not automatically safe. A manual managed-data correction
needs the canonical authorization, exact before/after, preserved recovery inputs,
exclusive writers and integrity verification. Preserve foreign WIP and claims, historical
stages and native receipts. Never infer an unknown external effect, acceptance or delivery.
Keep a real blocker explicit when no safe authorized route exists; do not invent success.
