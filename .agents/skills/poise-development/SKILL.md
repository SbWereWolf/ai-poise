---
name: poise-development
description: Change AI poise itself using its DDD, TDD, declarative-tool and project-local configuration rules.
---

# AI poise development

Read the repository `AGENTS.md` and `src/AGENTS.md` first. Use the current process' explicit `worktree_required` policy; without a required worktree, use the configured repository checkout. See [Task worktree placement](../../../docs/governance/development-rules.md#размещение-task-worktree).

## Design boundaries

Evaluate every proposed tooling change against the product's harness rule first: can the agent state the semantic intent declaratively while Poise owns the mechanical sequence and implementation details? Prefer designs that remove bookkeeping, internal storage knowledge and manual recovery choreography without taking away substantive engineering choices. Templates and routers accelerate planning; they are not hidden business constraints. A valid Task design that differs from its starter template should be represented by the resolved Task contract, not rejected because of template provenance.

Use the current [storage ownership/version matrix](../../../docs/architecture/storage-lifecycle.md#владельцы-и-версии) and [explicit portable-path mapping](../../../docs/architecture/storage-lifecycle.md#явные-пути-и-переносимая-поставка). Keep Task and canonical Requirements ownership separate; optional telemetry must not determine authoritative success. Do not infer arbitrary migration support from the limited v12 ownership upgrade.

Identify the domain owner before implementation. Task, Sprint, content, evidence, artifacts, project configuration and runtime state change only through their owning APIs. Reuse the common runner and stage-handler families rather than creating a goal-specific engine. Read [Architecture boundaries → owners and dependencies](../../../docs/architecture/boundaries.md#ddd-04b--новые-владельцы-и-зависимости).

Keep caller-to-session establishment in `SessionEstablisher` for every public work composition.
Use native identity when available and an explicit persistent `POISE_CALLER_BINDING` otherwise;
callers and agents must not substitute an arbitrary session value. Identity origin is provenance,
not authorization, and read-only establishment must not create a business subject.

A collaboration subagent is not an independent reviewer merely because its visible
name or role differs. When the configured producer and inspecting roles differ, require a distinct
effective session established by the common SessionEstablisher; never manufacture a session ID or caller binding. In Codex,
use a separate task/session, public handoff and bootstrap of the same Task, then
replay the exact progression request. Inspect `review_identity` provenance:
distinct effective actors do not prove live-host delivery or review quality.
See [reviewer identity](../../../docs/workflows/local-handoff.md#независимая-идентичность-проверяющего).

## Development method

For repository changes follow TDD: contract/check design → tests → honest RED → test inspection → implementation → GREEN → code inspection → fixes → reinspection → documentation. Read [Development rules → TDD and inspection](../../../docs/governance/development-rules.md#tdd-и-осмотр) when working on implementation behaviour.

For review, apply the incident-derived [evidence-based checklist](../../../docs/workflows/evidence-based-review.md#что-считать-находкой). Every actionable finding identifies the confirmed missing obligation, its producer stage, the later gate, an exact reproducible check and the evidenced cause class. Distinguish an AI poise runtime/task-contract defect, executor omission, reviewer omission, ordinary protective rejection and unresolved ownership. A historical repaired incident is regression provenance, not a current defect without new post-fix evidence. Batch independent Task/section/evidence/registry reads through the existing `show.queries`; never add another reader or claim unmeasured token savings.

For a development Task, pass the complete initial checks schedule explicitly; `{}` is valid and must not trigger template defaults. Design and register the actual RED/GREEN methods, schedules and future-output provenance at `verification_planning`, once their source paths are known.

Keep executable-obligation field presence schema-driven: process schemas with a
`test_registry` inspection route require the explicit field, while schemas without it reject
the field and initialize an empty registry classification. Permit an empty repository
`change_surface` only for a no-RED baseline guard whose sole GREEN is the route-entry
`baseline`; every produced-result GREEN needs a non-empty surface covered by its stage. Keep
the canonical semantics in [Batch work → Current verification registry](../../../docs/workflows/batch-work.md#текущий-реестр-методов-проверки).

Public non-newborn `bootstrap` and current-Task `show` projections expose the exact current Task version as `version`. The private `_version` name is never part of the public DTO. Taskless responses omit `version`, and newborn Tasks use `revision`. Pass this value unchanged as `expected_version` for guarded `restart` or stage-contract repair; on a version conflict, refresh through public `bootstrap`/`show` instead of guessing.

Tools are batch-oriented and declarative: if two or more required mechanical actions have no reasoning decision between them, expose one operation that ensures the requested result through owning APIs. For Task planning, the target model materializes goal-type templates as starter drafts, lets the planner reshape task-owned mutable fields before `ready`, freezes the resolved Task contract plus restart-revision policy at `ready`, and permits later contract revision only through authorized restart. Do not claim this target behavior before the runtime implements it. Do not add hidden defaults, compatibility readers or migrations without direct user authorization. Read [Declarative tools → architectural invariant](../../../docs/architecture/declarative-tools.md#1-архитектурный-инвариант) for the corresponding contract section available in the current revision.

Keep stale observation recovery inside the existing verification owner. An `observe` stage may
replace only one of its current `evidence_plan.subject_methods` through the guarded registry
change contract. Preserve its schedule, executable classification and coverage, keep historical
definitions and proof records immutable, and bind the first mutation/replay to one durable audit
identity. Structural registry edits remain owned by `test_registry`. Read the exact invariants in
[Evidence → stale observation replacement](../../../docs/workflows/evidence.md#замена-устаревшего-метода-наблюдения).

Keep the pending-resolution rework guard in the Task aggregate, before route or execution mutation.
Its error must identify only unresolved resolution IDs and the exact next inspection stage; clean
rework remains unchanged. Do not replace this invariant with a workaround Task or direct Task DB
repair. Read [Batch work → rework with pending resolutions](../../../docs/workflows/batch-work.md#rework-при-нерассмотренных-исправлениях) (Task 0063 RD-013).

## JetBrains MCP and structural fallback

For code-semantic work, treat configured endpoints, listed tools, worktree-bound observations,
and proof of a concrete operation as separate states. Use an applicable worktree-bound JetBrains
MCP capability for semantic navigation, call hierarchy, IDE inspections, semantic rename, or
IDE-owned formatting; select by tool semantics rather than a stored name map. Follow the exact
[selection rule](../../../docs/governance/jetbrains-mcp-policy.md#правило-выбора-инструмента) and
[boundaries](../../../docs/governance/jetbrains-mcp-policy.md#границы-и-исключения).

If the required IDE capability is unavailable or inapplicable, record the exact reason before
using ast-index for an operation it can replace. Use ast-index directly for repository-wide graph,
batch, and structural search. Do not claim that ast-index performed IDE inspection, semantic
refactoring, or IDE formatting. Follow [Fallback to ast-index](../../../docs/governance/jetbrains-mcp-policy.md#fallback-на-ast-index)
and preserve the [capability evidence](../../../docs/governance/jetbrains-mcp-policy.md#проверка-и-evidence) separately from configuration intent.

For AI-poise code navigation, use the existing [explicit-copy navigation proof](../../../docs/workflows/code-navigation.md#конкретная-папка-и-подтверждение-индекса)
when the configured provider supports it. Its protocol fixture is not live IDE evidence.
A concrete checkout path is the inspected subject; it must not replace live executable,
hook or configuration resolution. For automatic skill/rule/check selection, use the single
[bootstrap and refresh routing owner](../../../docs/configuration/development-routing.md#подключение-к-bootstrap-и-обновлению-контекста),
not a second IDE profile. Missing configuration or observations remain explicit gaps.
Read only the necessary candidate/receipt range before inspecting selected symbols.
IDE diagnostics remain supporting evidence, never substitutes for registered checks.

For Markdown, use an applicable IDE formatter first; otherwise invoke the
[explicit-file fallback](../../../docs/workflows/markdown-formatting.md#явное-форматирование-при-недоступной-ide)
with the actual reason. Do not chain another formatter after successful IDE formatting
or treat the read-only navigation helper as a mutation tool. See the canonical
[F1 tool application rule](../../../docs/governance/jetbrains-mcp-policy.md#применение-поставленных-инструментов-f1).

## Test cache identity and exclusive ownership

For AI-poise test cache work, follow the [content-hash contract](../../../docs/configuration/test-package-cache.md#хеши-байтов-по-группам-входов): include hashes of complete bytes separately for checked sources, tests and fixtures. Names, timestamps and sizes alone do not establish identity. Recalculate input bytes before reuse. The catalogue has only these three input categories; do not add a fourth digest category or a new wire schema. See the [key scope](../../../docs/configuration/test-package-cache.md#состав-целевого-ключа-и-сохраняемого-манифеста).

Use the [ordinary test workflow](../../../docs/configuration/test-package-cache.md#обычный-режим-выполнения-тестов): finish edits before running tests and resume editing afterwards. Do not add protections against an agent deliberately changing and restoring input files during the run. RV2-01 is withdrawn as outside the requested scope; do not turn it into a gate or a mandatory regression.

Use the existing [single-session worktree ownership](../../../docs/configuration/test-package-cache.md#монопольная-запись-через-владение): inputs, execution and cache belong to that checkout. The user withdrew the separate cache-lock/owner-recovery requirement; do not add concurrency machinery or turn it into a gate. Non-atomic index writes remain a documented known limitation, not an implemented guarantee. Read the [current status](../../../docs/configuration/test-package-cache.md#статус-выполнения-контракта). Check declared membership and the existing impact result after source changes; do not ignore unmapped paths or infer arbitrary dependencies with a new analyzer.

## Project-local configuration

AI poise is a separate application. Each configured project owns its Task DB and its copied process catalogue initialized from AI poise reference templates. Changing a reference template must not silently change an existing project's process configuration. For the WSL delivery model, read [Local installation → Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and [Project setup → Publication and replay](../../../docs/configuration/project-setup.md#публикация-и-повтор).

Keep target-project skill inventory and area routing out of AI poise source. Read the selected
project's `task_decomposition` policy and require every process phase to declare skills and
areas before readiness. Ordinary Tasks cannot combine peer narrow responsibilities. An integration Task names component_inputs, combined_result, integration_checks, and allowed_paths.
Every area routed to a policy-owned narrow responsibility must match a declared narrow-skill
responsibility regardless of phase; routes outside the policy's narrow responsibilities remain
non-narrow. Integration allowed paths cover every declared phase area. Validation does not prove factual completeness of declared skills or boundaries.

When another authorized owner has replaced a managed process file, diagnose the exact head
and live revisions with `goal-config-status-1`, then adopt validated content only with an exact
`goal-config-reconcile-1` request carrying reason and authority. Do not create a fresh editor database
to evade a stale managed head. Read [Goal config → revision reconciliation](../../../docs/configuration/goal-config.md#сверка-управляемой-revision-с-live-конфигурацией) before acting.

## Planned stage-skill tooling and current documentation

Apply the [planner-owned two-set draft](../../../docs/workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
when preparing future stage-skill work. The current slice changes documentation and
skills only; template fields, Task schemas, DB updates and enforcement need a later
explicit Task after the user's updated DB and planning. Reuse existing owners;
keep two roles and ordinary executable/hook path resolution. Report discovered
missing specialist skills in the final answer, not just an internal note.

For new commits follow [product effect and 50/70 formatting](../../../docs/workflows/commit-messages.md#продуктовый-смысл-и-формат-5070).
Use the existing owning workflow; no ERP commit wrapper or new validator is installed.
The [telemetry pre-persistence loss window](../../../docs/operations/telemetry-delivery.md#принятый-риск-до-сохранения-события)
is an accepted deferred risk, not a current blocker or authorization to repair it.

## Verification and delivery

An idle reviewer starts on a received handoff. If several handoffs arrive, finish the review already started, save its result, release the Task and notify its executor, then immediately take the next already-received actionable Task in the same turn. Incoming handoffs do not interrupt the current review. Follow the canonical [direct-handoff rule](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим).

Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks and the maintained bounded `tests/smoke.sh`; never register unfiltered repository-wide test discovery as a task method. Preserve failed diagnostic workspaces and do not replace a failed run with a retry. Package code, tasks/configs, documentation and verification evidence as one self-contained delivery.

Follow the canonical [executor/reviewer stage policy](../../../docs/governance/development-rules.md#роли-этапов-и-непрерывность-поручения). User Task start authorizes executor and reviewer to continue their respective stages, including checks and remediation, without a separate command for each review; honor explicit user limits and AI poise gates. At a role boundary, save results, confirm public handoff/release, stop modifying the Task and [directly notify the known counterpart](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим). The receiver claims/resumes before work. A configured single-role process permits self-inspection without a second participant. Self-inspection is not independent review, and publication/integration retains its separate authorization.

After release and notification, continue actionable received assignments or the authorized stage/Task chain in the same turn. Track assignments until completed, cancelled or explicitly transferred, including pending work and blockers in checkpoints and compaction context. New messages add work unless they explicitly change an assignment; acknowledgment is not completion. Before a final answer, account for every pending assignment; end only when no authorized actionable work remains, preserving each remaining blocker and next action. Finish your own already-started operations. Checking your own pending work is required; do not poll the other agent's status, read its conversation for progress or schedule background monitoring.

Before project setup, changing `git.base_ref`, or integration, read the canonical [target branch
selection](../../../docs/configuration/project-setup.md#выбор-ветки-основного-checkout-для-интеграции).
Treat the accepted commit as immutable. Complete delivery by advancing the existing task branch in its existing task worktree: update from the current configured target branch, merge, resolve conflicts there, and rerun every current produced-result GREEN registry method whose `green_stages` and `change_surface` are nonempty. The terminal content-stage schedule does not narrow integration checks; exclude RED and baseline-only guards. Do not create a separate integration branch or worktree. Stop automation on conflicts for agent resolution in the task worktree. Under the shared target lock, recheck the configured target and repeat the cycle on drift. Publish only with `git merge --ff-only <task-branch>` in the main checkout; never directly update or force-update the target ref.

Do not prepare, edit, or resolve conflicts in the main checkout or foreign WIP, and never `stash`, `reset`, `restore`, `checkout`, `clean`, stage, commit, or delete their state. The serialized fast-forward is the only publication effect. If it is blocked, require persisted proof that main `HEAD`, binding, index, tracked/untracked content, types, modes, and operation state are unchanged. After confirmed publication, remove only the task worktree, task branch, and registered temporary backups from the scoped runtime directory. Preserve foreign, operator, deliverable, and unfinished-recovery backups. Persist phases so current installed source can replay safely without rewriting the accepted commit. Read the canonical [finish and integration rules](../../../docs/governance/development-rules.md#интеграция-завершённого-результата).

Repository snapshots use an invocation-owned temporary Git index and must clean only that exact owned directory. Never delete `snapshot.index.lock` manually or remove another invocation's directory; preserve real Git conflict evidence and use the owning public recovery path. Read the canonical [snapshot index contract](../../../docs/workflows/batch-work.md#изоляция-временного-git-index).

When a skill relies on canonical documentation, link to the smallest exact normative section needed by the operational rule.


## Inputs for behavior and verification work

Use the current Task/stage contract, real owner, observable result, source/runtime identity and selected Poise-owned commands/configuration. Inspected-copy manifests and documentation are facts, not a place to persist or replace the running Poise routing configuration.

## Procedure for migrated development skills

For changed executable behavior load [tdd](../tdd/SKILL.md) and its [independent test-data policy](../../references/test-data-and-assertion-policy.md). For selecting/running checks load [direct-checks](../direct-checks/SKILL.md). For a terminal failure or confirmed loss of its runner load [debugging-and-recovery](../debugging-and-recovery/SKILL.md); no competing active-run observer or blind retry is authorized.

Ordinary Task completion and integration use only targeted behavior/boundary checks plus maintained fast smoke. Do not run full/unfiltered regression during task work; unresolved target selection never falls back to all tests. Preserve C004/C016/C017 ownership and C018 cache semantics. Current Poise verification-method declarations do not have a per-method timeout; infrastructure runner limits remain owned by their configured runner. Do not reintroduce method timeouts, copied ERP command wrappers, mandatory serializer packages or manual review registries through these skills.

## Output from migrated development skills

Persist exact test/requirement/RED/GREEN/inspection and terminal check references through existing Task/evidence owners. Report unavailable checks and capability gaps. Static instruction links provide discovery, not proof that the configured router or an IDE operation actually ran. Independently reviewed acceptance and result integration remain separate gates.

## Shared skill metadata

Use the explicit [skill catalog contract](../../../docs/configuration/skill-catalog.md)
for skill identity, path, purpose and specialization. `SkillCatalog` owns metadata;
routing and decomposition consume it rather than copying instruction bodies or
inferring classes from names. `poise skills` reads metadata without Task ownership.

## Resumable work

After each task and before a risky transition apply [checkpoint and recovery](../../../docs/workflows/checkpoint-recovery.md#контрольная-точка-и-восстановление) using `tools/work_checkpoint.py` and verify the checkpoint locally. Gmail delivery and full attachment readback are mandatory only for cloud development. In local development they require an explicit request and must not block Task completion, integration or Sprint continuation. On resume inspect provided attachments first. Preserve explicit next work and rejected baselines.

For authoring-stage Python boundary diagnostics and same-Task repair, use
[the configured architecture gate](../../../docs/workflows/architecture-boundaries.md#проверка-архитектуры-перед-авторской-сдачей).
This is AI-poise-specific, not a multi-language analyzer for every managed project.

For a reported hook/runtime problem, use the explicit
[hook effect diagnostics](../../../docs/operations/hook-effect-diagnostics.md#диагностика-фактических-эффектов-hooks).
`runtime-config` accepts a declarative `diagnose` batch; it observes existing owners without
bootstrapping work or replaying telemetry. Supply a concrete `probe_cwd` only when actively
requesting the configured probes. A current-process provenance path is not another resolution root.

## Accidental cancellation recovery

Use the public `task` action `recover_cancelled` only with real explicit user
permission, exact `task_id`/`expected_version`, a stable `request_id`, nonempty
`reason` and `authorization`. This restores the exact prior unfinished state;
it is not `restart`, does not acquire a claim and never edits Git/WIP. Preserve
its audit and use ordinary bootstrap afterwards. Completed/integrated work,
foreign/ambiguous ownership, pending external effects or cleanup with a chosen
commit disposition must not be bypassed. An old cancellation without a precise
recovery point is not guessed. Do not issue direct Task DB edits.
Read [the exact packet and guards](../../../docs/workflows/batch-work.md#восстановление-ошибочно-отменённой-task).

## Requirements Registry owner

Keep canonical System/Application data in the explicitly configured Requirements DB;
Task owns immutable agreed lineage. Route public planning queries and atomic changes
through RequirementsCommands, and Task/Sprint publication through TaskRequirementsGate.
Do not add implicit storage paths or rewrite historical Task context. See
[storage ownership](../../../docs/workflows/requirements-registry.md#владение-и-явные-пути)
and [publication](../../../docs/workflows/requirements-registry.md#публикация-и-исторический-снимок).

## Accepted-stage replay after restart

Commit all owned WIP in the Task worktree before automatic replay. Replay uses
the same worktree and branch, preserves a durable recovery ref before reset,
and rechecks accepted stages at their exact accepted commits. Passing historical
tests and unchanged registered proof files suffice when current proof obligations
remain compatible. Restart or requirement wording alone does not invalidate proof.
Never substitute old methods, expectations, schedules or evidence plans for an
explicitly changed current contract. Revalidate ignored collisions and preservation
before every destructive checkout, including resume. Mechanical replay is role-neutral; new substantive work retains
independent review. Omit the target for maximum progress; an explicit target stops
on entry before its checks. Never rerun an unknown command outcome. A completed
request replays its saved result; use a new identity for new progression.
Follow the [canonical replay
contract](../../../docs/workflows/batch-work.md#автоматическая-промотка-после-перезапуска).
