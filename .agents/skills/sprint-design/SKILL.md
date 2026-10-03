---
name: sprint-design
description: Design a connected dependency-aware Poise Sprint from bounded Task drafts without designing their future implementation.
---

# Dependency-aware Sprint design

Use when preparing a Sprint graph and its bounded Task drafts. Delegate each draft's
formulation discipline to [task-design](../task-design/SKILL.md), not to a prototype.
Use `exec-task` for execution. Planning does not grant publication or implementation
authority beyond the assignment. Follow the canonical
[separation of planning and
execution](../../../docs/workflows/paired-task-stages.md#постановка-планирование-спринта-и-исполнение).

## Inputs

The feature goal, confirmed requirements/DoD, application/owner boundaries, existing contracts and
results, target-project facts from the explicitly selected checkout path, and the selected
Poise-owned project/process/routing configuration. The [existing decomposition
contract](../../../docs/workflows/batch-work.md#декомпозиция-и-фокус-task) and [Sprint
owner](../../../docs/workflows/sprints.md#владелец-и-основные-гарантии) define the actual schema.

## Draft: planner-owned stage skill assignments

Use the [two-set
proposal](../../../docs/workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
when drafting the next Task: seed both meta skills (how this stage is performed)
and subject skills (its concrete expertise) from its selected stage template, then
remove inapplicable entries and add needed ones as the planner. Plan each real stage,
including test/code inspection and remediation, not one Task-wide substitute list.
The resulting assignment belongs to the Task; later template changes must not
silently replace it. These are draft requirements for future tooling, not supported
new API fields. Keep using only the current schema described below; do not edit
managed configuration/DB or create a new task until planning is authorized.
Distinguish executor/reviewer roles from meta-skill specializations. Report any
missing specialist skill, its stage and impact in the final answer for the planner.

## Procedure

Sprint design owns goals, actual dependencies, coverage and the order of obtaining
results. Task creation owns initial goal/DoR/DoD and known facts. Executing stages
own detailed check, code and documentation design. Do not require future test names
or exact commands at Sprint creation, or implement code to justify its initial plan.

1. Identify one coherent, verifiable result per ordinary Task. Follow real responsibilities, not
   folder or application count. Split unrelated areas or peer narrow specialties even inside one
   application or across different stages. General/meta skills alone do not force a split.
2. Declare each stage's inputs, outputs, role, skills and own write scope. Distinguish routed areas,
   stage `allowed_paths` and method inputs using [future paths and stage
   boundaries](../../../docs/workflows/task-planning.md#будущие-пути-и-границы-этапа); permitted
   output paths need not exist yet.
3. Read skill classes and responsibility routes from the selected project's `task_decomposition` and
   the [decomposition contract](../../../docs/workflows/batch-work.md#декомпозиция-и-фокус-task).
   Match every phase and narrow area honestly; declarations alone do not prove completeness.
4. For a true integration goal, reference component results and define one combined result,
   integration checks, and allowed paths covering all phase areas. Use the actual
   `component_inputs`, `combined_result`, `integration_checks`, and `allowed_paths` fields. Several
   areas/narrow skills are allowed only for that combined integration result, not as a label to
   bypass decomposition.
5. Create/edit/ready Tasks and draft/materialize Sprints through existing batch operations. Preserve
   permanent IDs, history, membership and WIP on a lawful correction; never allocate a role quartet,
   invent a numeric score, or add an ERP registry.
6. Record dependencies and future inputs at the stage where they become available. Distinguish
   sequencing from the [result
   dependency](../../../docs/workflows/sprints.md#два-явных-вида-зависимостей) that exposes a
   component result. Declare exact future source paths and producer stages for verification methods
   when required.
7. Use the owning validator and consume its complete returned diagnostics as one batch. `poise
   skills` with `operation=validate` checks explicit Task declarations without bootstrap, claims or
   a Task DB; it calls the same `FocusedDecomposition` owner. It reports all independently
   detectable structural/classification violations, including other Tasks after an invalid Task.
   Checks requiring malformed data are skipped rather than invented. Direct validation neither
   publishes a Sprint nor attests component provenance.
8. Inspect the substantive honesty of the split, declared skills, boundaries, expectations and
   future-input feasibility. The agent/reviewer owns that judgment. Correct defects through public
   Task/Sprint operations and preserve exact optimistic versions/request identities.
9. Save the plan, graph, stage inputs/outputs and outstanding dependencies in existing managed
   content. Keep preparation/readiness distinct from the separately authorized [Sprint
   publication](../../../docs/workflows/sprints.md#опубликовать).

## Output

A persisted focused plan with explicit per-stage contracts, dependencies, component provenance and
future inputs; a batch of structural diagnostics; and the remaining substantive
decisions/limitations. No new lifecycle, role quartet, mandatory ERP registries or implicit
publication is introduced. Required automatic routing and complete diagnostic aggregation must exist
in Poise itself; a skill file does not implement them.
