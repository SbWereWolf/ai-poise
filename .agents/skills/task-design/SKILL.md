---
name: task-design
description: Formulate a bounded Poise Task draft from known facts without implementing its future solution.
---

# Bounded Task formulation

## Registration and execution preparation

Follow the [shared contract](../../../docs/workflows/task-execution-contract.md#постановка-и-подготовка-задачи). The registration API may record only the stated goal and existing materials. Initial formulation uses available facts and bounded source inspection to state the goal, draft DoR/DoD and a feasible overall path. Detailed stage design is not a creation gate. Research the actual subject; code research applies only to code tasks. Every requirement condition must change meaning. Do not perform future-stage work. The executor prepares the work; the reviewer reviews it.

Use for creating or revising one authorized Task draft. Use
[sprint-design](../sprint-design/SKILL.md) for a dependency graph and
[exec-task](../exec-task/SKILL.md) for execution. Read the canonical
[formulation
contract](../../../docs/workflows/paired-task-stages.md#постановка-планирование-спринта-и-исполнение).
Apply the canonical [product goal and requirements
rule](../../../docs/workflows/paired-task-stages.md#цель-и-требования-к-продукту).

## Inputs

The authorized result, existing code and interfaces, environment facts, known
requirements, constraints and uncertainties, and the explicitly selected project's
workflow, routing and supported public Task schema.

## Procedure

1. When beginning execution preparation, investigate the actual subject and conditions.
   For code tasks use applicable semantic tools. Do not build a prototype for registration.
   Separate observations from assumptions.
2. Preserve the registered goal and determine requirements and achievable DoD from current
   conditions, without performing future execution stages. For product development, keep
   incident history and concrete installation facts in the factual basis,
   architectural obligations in constraints, and implementation choices in their
   execution stages. Use the selected Task schema, not invented rationale fields.
   Combine related checks of one behavior without dropping obligations or imposing
   a fixed requirement count. Record exclusions and unknowns. Explain logical
   consistency and bounded feasibility; structural validation alone does not prove
   feasibility or product formulation quality.
   Write human-facing AI poise goals, requirements and acceptance criteria in
   Russian, including within managed Task artifacts.
   Describe positive requested behavior only; excluded actions belong in separately
   agreed constraints. Use stable application/state concepts and keep adjacent scenarios
   outside the authorized scope. Apply the linked product formulation rule above.
   Apply [domain language and implementation freedom](../../../docs/workflows/paired-task-stages.md#предметный-язык-требований-и-свобода-реализации)
   at System, Application and Task levels: use business/domain entities and
   observable behavior, outcomes, environment conditions, meaningful values and
   data. Keep application names, scripts, classes, packages, paths and other
   implementation details out of requirements. Preserve explicit technical user
   decisions separately as decisions or scoped constraints with their source;
   otherwise the executor chooses the implementation. Record accepted implementation
   recommendations separately, with enough context to understand the question,
   choice, rationale, scope and consequences without running a checker. Additional
   verification of that record uses only reading code, documentation and tests; it does
   not replace the workflow-owned behavioral checks or prove that tests passed.
3. Do not predict future class names, test names or commands as readiness gates.
   Supply the complete initial checks object explicitly; an empty object is valid
   for a development Task. Concrete test and code design belong to execution.
4. Declare explicit stage purpose and criteria authority using the canonical
   [stage criteria contract](../../../docs/workflows/stage-criteria.md), separately
   from stage roles and handlers. Supply initial stage criteria from known facts.
   Declare actual stage roles, inputs/outputs, skills and sufficient write boundaries
   using the selected project's decomposition contract. A paired workflow requires
   independent review after each substantive executor result, not role impersonation.
5. Use only public create/edit/ready and contract APIs. Preserve identity, ownership,
   history and source facts. Do not change the registered goal.
6. Submit the draft for independent review of scope, feasibility, responsibility
   and allowed_paths. Paths may be narrow or explicitly broad; the reviewer checks
   sufficiency and stage separation, not just whitelist syntax.
   Review every requirement against the goal and its observable result. Reject
   incident narrative, implementation instructions or single-installation service
   work presented as product requirements; one live installation is not proof of
   the full declared product scope.
7. Keep Task draft edits separate from future execution-stage criteria proposals
   and decisions through `verify.criteria_work`. Follow the canonical stage criteria
   contract for authority, exact revisions and execution binding. Changes to bound
   criteria require an authorized restart; do not invent a combined restart endpoint.

Apply [just-in-time stage design](../../../docs/workflows/task-execution-contract.md#проектирование-непосредственно-перед-этапом): agree the overall stage sequence first; prepare and independently review only the imminent stage's goal, requirements and acceptance criteria before execution. Ordinary refinement of an unstarted stage is not a reason to restart the Task.

## Output

A persisted bounded draft, its factual basis, initial criteria, explicit uncertainties,
structural diagnostics and remaining substantive review. No future implementation,
new environment, implicit Sprint membership or publication authority.
