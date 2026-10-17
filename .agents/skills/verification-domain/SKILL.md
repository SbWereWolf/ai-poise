---
name: verification-domain
description: Change Poise check registries, obligation coverage and evidence acceptance through their owners; target test frameworks and external command execution retain their own tools.
---

# Verification domain

Own verification registries, obligation coverage, schedules and evidence validation.
The delivered working skill has level 1 and maps to narrow responsibility `verification`.

Poise owns Task verification contracts and results. Reuse the existing execution
boundary for the external harness, including the ERP harness as it moves into
Poise; registry changes do not require another runner, controller or receipt store.
Keep command execution, receipt retention and stage finalization distinct so
a read failure after a completed command cannot silently cause another execution.

Design obligations and independent oracles at their owning stage. Declare the
earliest GREEN stage and genuine RED predicate. Trace method inputs, coverage
and schedules through the current registry owner; a stage label does not grant
replacement authority. Use [one evidence packet](../../../docs/workflows/evidence.md#один-пакет-результата).

Bind proof to the actual commit, tree, inputs and terminal outcome. Read complete
saved stdout/stderr and structured fields for machine decisions; preview is
presentation. Follow [freshness and replay](../../../docs/workflows/evidence.md#актуальность-и-повтор)
and [output views](../../../docs/workflows/runner.md#три-представления-вывода).
Preserve unknown outcomes and inspect effects before retrying. Historical receipt
copies do not prove a changed source.

Check coverage refusals, provenance and continuation through the real public
boundary with independent expectations. Keep self-review distinct from independent
review and verification distinct from user acceptance. Storage changes use its owner.
