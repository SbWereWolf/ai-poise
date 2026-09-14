---
name: sprint-design
description: Prepare focused Poise Tasks and dependency-aware Sprints with explicit stage inputs, outputs, roles, skills and write boundaries.
version: 1.0.0
---

# Focused Task and Sprint design

Use when preparing a standalone Task or Sprint. Use `exec-task` for execution; planning does not grant publication or implementation authority beyond the assignment.

## Inputs

The feature goal, confirmed requirements/DoD, application/owner boundaries, existing contracts and results, target-project facts from the session worktree, and the selected Poise-owned project/process/routing configuration. The [existing decomposition contract](../../../docs/workflows/batch-work.md#декомпозиция-и-фокус-task) and [Sprint owner](../../../docs/workflows/sprints.md#владелец-и-основные-гарантии) define the actual schema.

## Procedure

1. Identify one coherent, verifiable result per ordinary Task. Follow real responsibilities, not folder or application count. Split unrelated areas or peer narrow specialties even inside one application or across different stages. General/meta skills alone do not force a split.
2. For every Task and stage, specify input, output, executor or reviewer, required skills and exact writable areas. Use existing Task/process content and stage contracts for inputs/outputs/roles. In `decomposition.phases`, use only its real fields: `stage`, `skills`, and `areas`; do not invent extra schema keys.
3. Read skill classes, narrow responsibilities and area mappings from the selected Poise project's `task_decomposition`, not a hard-coded ERP map. The [shared catalog](../../../docs/configuration/skill-catalog.md) supplies metadata through an explicit project snapshot; do not maintain another class map. Every narrow area needs its matching skill in the same phase, not a different phase. Match every process phase exactly and include all relevant areas honestly. A technically valid declaration does not prove that the inventory or scope is complete.
4. For a true integration goal, reference component results and define one combined result, integration checks, and allowed paths covering all phase areas. Use the actual `component_inputs`, `combined_result`, `integration_checks`, and `allowed_paths` fields. Several areas/narrow skills are allowed only for that combined integration result, not as a label to bypass decomposition.
5. Create/edit/ready Tasks and draft/materialize Sprints through existing batch operations. Preserve permanent IDs, history, membership and WIP on a lawful correction; never allocate a role quartet, invent a numeric score, or add an ERP registry.
6. Record dependencies and future inputs at the stage where they become available. Distinguish sequencing from the [result dependency](../../../docs/workflows/sprints.md#два-явных-вида-зависимостей) that exposes a component result. Declare exact future source paths and producer stages for verification methods when required.
7. Use the owning validator and consume its complete returned diagnostics as one batch. `poise skills` with `operation=validate` checks explicit Task declarations without bootstrap, claims or a Task DB; it calls the same `FocusedDecomposition` owner. It reports all independently detectable structural/classification violations, including other Tasks after an invalid Task. Checks requiring malformed data are skipped rather than invented. Direct validation neither publishes a Sprint nor attests component provenance.
8. Inspect the substantive honesty of the split, declared skills, boundaries, expectations and future-input feasibility. The agent/reviewer owns that judgment. Correct defects through public Task/Sprint operations and preserve exact optimistic versions/request identities.
9. Save the plan, graph, stage inputs/outputs and outstanding dependencies in existing managed content. Keep preparation/readiness distinct from the separately authorized [Sprint publication](../../../docs/workflows/sprints.md#опубликовать).

## Output

A persisted focused plan with explicit per-stage contracts, dependencies, component provenance and future inputs; a batch of structural diagnostics; and the remaining substantive decisions/limitations. No new lifecycle, role quartet, mandatory ERP registries or implicit publication is introduced. Required automatic routing and complete diagnostic aggregation must exist in Poise itself; a skill file does not implement them.
