---
name: poise-workflow
description: Execute project-local AI poise task stages and hand off work between executor and reviewer through declarative batch tools.
---

# AI poise task workflow

## Command quick reference

Use the [complete CLI
reference](../../../docs/operator/poise-command-reference.md#запуск-и-общие-аргументы)
for argument meanings, prerequisites and canonical packet owners.
Use the [environment
entry](../../../docs/operator/poise-command-reference.md#обслуживание-окружения)
for the setup instruction immediately below its target heading.
The prepared standalone Bootstrap and installed delegated maintenance CLI are distinct.

Examples assume explicit absolute paths and a valid JSON packet for the selected
owner. Define `poise=( "${poise_python:?Select installed Poise Python}" -I -B -m poise )`.
Here `settings` means the settings of the particular operation, not one universal
file. Operator examples document capabilities; they do not authorize mutations.
For Task work retain the native hook-issued session launcher and caller identity.
Read its exact path from the current native context, including after `fork`;
`AI_POISE_WORK` is not automatically exported. An entry delivered with a runtime
warning is available for recovery, not evidence that Task work is ready. See
[session entry delivery](../../../docs/configuration/runtime-hooks.md#доставка-launcher-при-старте).
For worktree-wide discovery, use the public `runtime-config` reconfiguration
owner and the explicit shared repository scope; retain the common native-file
writer lock. Follow [shared
installation](../../../docs/configuration/runtime-hooks.md#общая-установка-для-рабочих-деревьев).
Host hook trust and genuine native delivery remain separate observations.

### Task work and reads

[Arguments and
packets](../../../docs/operator/poise-command-reference.md#работа-и-чтение-результатов).

| Command | Purpose | Correct example |
|---|---|---|
| `work` | Execute one declarative Task/Sprint packet. | `bash "${native_launcher:?Exact path from native context}" < "$packet"` |
| `result-view` | Read a saved bounded view without replay. | `"${poise[@]}" result-view --response "$response" --view expanded --output-chars 3000` |
| `next` | List startable Tasks without claiming. | `"${poise[@]}" next --settings "$settings" --project "$project_id"` |
| `requirements` | Query/change Requirements through their owner. | `"${poise[@]}" requirements --config "$project_json" < "$packet"` |
| `placement` | Prepare/check explicit working-result placement without requiring a Task. | `"${poise[@]}" placement --config "$project_json" --checkout "$checkout" --policy "$placement_policy" < "$packet"` |
| `skills` | Read metadata, validate decomposition, prepare file-scoped instructions or deliver current-context materials without a Task. | `"${poise[@]}" skills --catalog "$skill_catalog" --root "$skill_root" < "$packet"` |
| `checks` | Run/read configured named checks without a Task. | `"${poise[@]}" checks --config "$project_json" --checkout "$checkout" --policy "$checks_policy" --view expanded < "$packet"` |

Named-check setup, exact operations, effective cwd/PATH, saved results and recovery
belong to the [operator contract](../../../docs/configuration/named-project-checks.md).
Retain the genuine native caller and omit `POISE_CALLER_BINDING`, or use an explicit
persistent binding in a separate direct process without native session variables.
Do not combine those identities or change the session launcher to prepare a check.
For independently reviewing test-only RED in an unfinished `revise` stage, use the
existing [partial-material
handoff](../../../docs/workflows/local-handoff.md#незавершённые-материалы-исправления):
save immutable materials through public artifacts, then ordinary release of the same Task.
Do not invent resolution proposals or native stage completion before actual fixes.

### Current-context instruction delivery

Use `skills/deliver` for assigned stage skills, one explicitly selected tool guide
and current-context reuse. A receipt alone proves no retained text; confirm keys
only after receiving their complete bodies. Missing receipt or context loss uses
full current delivery. Preserve full machine output and use the existing common
delivery/error projection for agents. Keep native caller identity unchanged.
Use the [short guide](../../references/skills-delivery.md) and the canonical
[setup, packet and recovery
contract](../../../docs/configuration/instruction-delivery.md#вызов-и-подготовка).

### Project and workflow configuration

[Arguments and packets](../../../docs/operator/poise-command-reference.md#проект-и-его-настройки).

| Command | Purpose | Correct example |
|---|---|---|
| `project` | Create a configured project from one packet. | `"${poise[@]}" project --settings "$settings" < "$packet"` |
| `project list` | List configured projects. | `"${poise[@]}" project list --settings "$settings"` |
| `project check` | Check the explicitly selected project. | `"${poise[@]}" project check --settings "$settings" < "$packet"` |
| `project-config` | Update an existing project's settings. | `"${poise[@]}" project-config --settings "$settings" < "$packet"` |
| `project-init` | Prepare a selected template interactively. | `"${poise[@]}" project-init --settings "$settings" --template "$template_id" --destination "$destination" --request-id "$request_id" --probe-repository yes` |
| `goal-config` | Edit goal/workflow configuration declaratively. | `"${poise[@]}" goal-config --settings "$settings" < "$packet"` |
| `catalogue` | Install workflows or prepare typed Tasks. | `"${poise[@]}" catalogue --settings "$settings" < "$packet"` |

### Runtime and hooks

[Arguments and packets](../../../docs/operator/poise-command-reference.md#runtime-и-native-hooks).

| Command | Purpose | Correct example |
|---|---|---|
| `runtime` | Submit identity/capabilities/transcript and one work packet. | `"${poise[@]}" runtime --settings "$settings" < "$packet"` |
| `runtime-setup` | Create explicit hook settings and install. | `"${poise[@]}" runtime-setup --settings "$settings" --max-input-bytes 262144 < "$packet"` |
| `runtime-config` | Configure hooks or run installation probes. | `"${poise[@]}" runtime-config --settings "$settings" < "$packet"` |
| `hook` | Deliver an actual native event using its immutable definition. | `"${poise[@]}" hook --settings "$settings" --definition "$definition" < "$native_event"` |
| `hook-work` | Submit work with the hook-generated session binding. | `"${poise[@]}" hook-work --settings "$settings" --binding "$binding" < "$packet"` |

### Environment maintenance

[Two-word entry and setup
instructions](../../../docs/operator/maintenance-profile-v2.md#обычные-команды-start-app).
Use the installed `start-app` from the serviced checkout; the project supplies persistent JSON.

| Command | Purpose |
|---|---|
| `start-app infra` | Prepare infrastructure and its tool prerequisites. |
| `start-app deps` | Install application dependencies. |
| `start-app adjust` | Apply the configured project adjustment request. |
| `start-app check` | Check declared resources and configuration. |
| `start-app run` | Execute the configured launch chain. |


Use the [SDK2 instance/action
setup](../../../docs/operator/maintenance-profile-v2.md#выбор-экземпляра-и-действия)
and [Task-worktree
configuration](../../../docs/operator/start-app-worktree-setup.md#подключение-установленного-инструмента).
Use the [persistent ERP CLI dependency
projection](../../../docs/operator/maintenance-profile-v2.md#дополнительные-cli-зависимости-erp)
so fresh preparation includes the calculator's Composer dependencies.
Deploy through the configured standalone tool; update every actual registered consumer through
[the mandatory
gate](../../../docs/operator/maintenance-profile-v2.md#согласованное-обновление-потребителей).
For preserved legacy ERP settings use the [owner
migration](../../../docs/operator/maintenance-profile-v2.md#сохранённые-настройки-erp)
and obtain a fresh native preparation receipt afterward.

Use the [own-instance
setup](../../../docs/operator/maintenance-profile-v2.md#развёртывание-из-примера)
for the complete operation profile, working settings, preparation/check/run example.
[Materializer arguments](../../../docs/operator/maintenance-profile-v2.md#аргументы-материализатора)
and [Bootstrap
configuration/results](../../../docs/operator/bootstrap-catalog-v2.md#конфигурация-команды)
own the details. Keep the service Python and installed SDK Python distinct.

| Command | Purpose | Correct example |
|---|---|---|
| `poise_environment.profile --profile-spec` | Publish the explicit own-instance operation profile without preparing state. | `PYTHONPATH="$poise_source/src:$poise_source" "$poise_python" -P -s -B -m poise_environment.profile --source "$poise_source" --profile-spec "$poise_source/config/maintenance/start-app/profile.example.json" --output "$profile" --state "$state_root" --repository "$repository" --project "$project_id" --base-ref "$base_ref" --author-name "$author_name" --author-email "$author_email" --module-path "$poise_source/src"` |
| `bootstrap infra` | Execute the configured infrastructure chain. | `"$poise_python" -I -B "$poise_source/bootstrap" infra --config "$profile/bootstrap.json" --base-dir "$repository"` |
| `bootstrap deps` | Execute the configured dependency chain. | `"$poise_python" -I -B "$poise_source/bootstrap" deps --config "$profile/bootstrap.json" --base-dir "$repository"` |
| `bootstrap check` | Check declared presence, availability and additional resource conditions. | `"$poise_python" -I -B "$poise_source/bootstrap" check --config "$profile/bootstrap.json" --base-dir "$repository"` |
| `bootstrap run` | Execute only the configured launch chain. | `"$poise_python" -I -B "$poise_source/bootstrap" run --config "$profile/bootstrap.json" --base-dir "$repository"` |
| `worktree_profile.py publish` | Publish declared SDK2 ERP documents from the selected target checkout. | `"$poise_python" -B "$publisher" publish --spec "$application_spec" --source "$repository" --output "$profile" --parameters "$supervisor_parameters"` |
| `bootstrap` with retained catalog profile | Use the separate historical consumer's compatible command and context. | `"$poise_python" -I -B "$poise_root/bootstrap" check --config "$catalog_bootstrap_config" --catalog-dir "$catalog_profile"` |
| `poise infra` | Delegate to the separately selected requirements CLI. | `"${poise[@]}" infra --catalog "$requirements_catalog" --repairs "$repairs_file" --dry-run` |
| `poise deps` | Delegate dependency maintenance through that retained interface. | `"${poise[@]}" deps --catalog "$requirements_catalog" --values "$values_file" --dry-run` |
| `poise check` | Delegate observation through that retained interface. | `"${poise[@]}" check --catalog "$requirements_catalog" --values "$values_file"` |

SDK2 operation examples (the full argument/parameter contract belongs to the linked instruction).
The `adjust` example selects the ERP profile declaring that operation; the own Poise app
declares only infra/deps/check/run:

| Command | Purpose | Correct example |
|---|---|---|
| `start-app infra` | Execute configured infrastructure commands. | `"$start_app" infra --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository" --requirements all` |
| `start-app deps` | Execute configured dependency chains. | `"$start_app" deps --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository" --requirements all` |
| `start-app adjust` | Execute declared coordination after its prerequisites. | `"$start_app" adjust --config "$erp_profile/app.json" --parameters "$erp_profile/parameters.json" --base-dir "$erp_repository" --requirements all` |
| `start-app check` | Observe resources and additional checks. | `"$start_app" check --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository"` |
| `start-app run` | Execute only the declared launch chain. | `"$start_app" run --config "$profile/app.json" --parameters "$profile/parameters.json" --base-dir "$repository" --requirements all` |

Populate ignored working files before executing; source examples stay versioned.
The selected plugin owns complete result interpretation and one invocation-local
permitted repair/check; subsequent errors return to the agent. Use explicit selected
roots; Task worktrees belong with the serviced repository outside mutable state.
[All Bootstrap
arguments](../../../docs/operator/bootstrap-catalog-v2.md#подготовка-и-безопасная-проверка)
separate base-dir operations from the retained catalog forwarding contract.

[Automatic Task-worktree
preparation](../../../docs/operator/worktree-preparation.md#автоматическая-подготовка-обоих-проектов)
retains its own installed start-app settings, genuine Task coordinates and receipts.
[Preparation configuration](../../../docs/operator/worktree-preparation.md#опции-рабочего-json)
owns all options, inputs, streams and retry reconciliation.
[ERP setup](../../../docs/operator/maintenance-profile-v2.md#профиль-erp),
[ERP roots](../../../docs/operator/maintenance-profile-v2.md#параметры-и-корни-erp)
and [ERP process
diagnostics](../../../docs/operator/maintenance-profile-v2.md#процессы-и-диагностика-erp)
remain separate. For existing Stride maintenance overrides follow the
[explicit cutover/return
procedure](../../../docs/operator/maintenance-profile-v2.md#переход-на-новый-маршрут-и-возврат)
and [its command
examples](../../../docs/operator/poise-command-reference.md#обслуживание-catalogv2-через-stride).

For observed harness limitations, use the
[replay](../../../docs/operations/known-bugs.md#stage-replay-semantic-001--историческая-промотка-не-выполняет-новое-проектирование),
[lock](../../../docs/operations/known-bugs.md#aip-external-lock-diagnostics-001--отказ-не-называет-держателя)
and [IDE
source-preservation](../../../docs/operations/known-bugs.md#aip-ide-format-source-preservation-001--потеря-правок-после-форматирования)
guidance; apply the actual current-stage recovery owner, not a blanket restart.
For historical snapshot metadata refusals, follow the
[verified recovery
procedure](../../../docs/operations/known-bugs.md#восстановление-сохранённых-снимков-этапов):
inventory Sprint drafts as well as Tasks, use the public owner first, and preserve
the exact authorized scope and audit for a necessary manual correction.
Before an agreed restart, both roles check the actual
[progression and current
position](../../../docs/workflows/pilot-task-preflight.md#сверка-продолжения-перед-restart).
Publish permanent proofs while ACTIVE before verify; use saved native receipts
afterward. Follow the [handoff material
timing](../../../docs/workflows/local-handoff.md#постоянный-комплект-при-передаче).

### Operator recovery and migrations

[Arguments, authority and
procedures](../../../docs/operator/poise-command-reference.md#локальные-файлы-резервные-копии-и-миграции).

| Command | Purpose | Correct example |
|---|---|---|
| `local-assets` | Restore declared files from an explicit snapshot. | `"${poise[@]}" local-assets --manifest "$manifest" < "$packet"` |
| `backup help` | Read backup/operator-window guidance. | `"${poise[@]}" backup help` |
| `backup list` | List Task DB backups. | `"${poise[@]}" backup list --config "$project_json"` |
| `backup create` | Create a backup with writers stopped. | `"${poise[@]}" backup create --config "$project_json"` |
| `backup restore` | Restore a named backup with writers stopped. | `"${poise[@]}" backup restore --config "$project_json" "$backup_name"` |
| `route-migrate` | Apply the authorized route migration. | `"${poise[@]}" route-migrate --config "$project_json"` |
| `task-workflow-migrate` | Migrate an explicitly authorized workflow-snapshot batch. | `"${poise[@]}" task-workflow-migrate --config "$project_json" --project-settings "$settings" < "$packet"` |

Keep brief and expanded agent views distinct and within configured budgets.
Full output is always a saved-file link, not inline data. Preserve complete machine
results and actual operation outcomes. Follow the canonical
[three-view contract](../../../docs/workflows/runner.md#три-представления-вывода).
Use `poise result-view` with an explicit saved path, view and character budget.
Never replay a mutation to read its output; read success is not business success.
Follow [read-only
navigation](../../../docs/workflows/runner.md#чтение-сохранённого-результата-без-повторного-выполнения).

For explicit stage purpose, criteria authority, `show` with `stage_criteria` and
`verify.criteria_work`, read the canonical [stage criteria
contract](../../../docs/workflows/stage-criteria.md). Read actual revisions and
proposal identities through their owners; do not infer authority from role or
handler names, edit managed storage, or treat criteria work as a general Task edit.

For explicit stage-criteria migration, a genuine native runtime-resources bootstrap receipt
may prove the source revision. Preserve its original identity and saved bytes; use the
ordinary migration update receipt to prove completion. Follow the canonical
[source and recovery
contract](../../../docs/task-workflow-snapshot-migration.md#публикация-атомарность-и-восстановление).

AI poise is a declarative harness whose job is to remove mechanical administration and tooling
implementation details from the agent's work. Templates accelerate planning; they must not become a
reason to force a valid Task into a starter shape. The planner owns the newborn draft before
`ready`; execution consumes the resolved Task contract.

Use the explicitly selected project configuration. Each project owns its Task DB and its copied
workflow catalogue; never
edit managed SQLite, workflow JSON, or task artifacts as bookkeeping.

For an existing Task, use its saved process and current public stage, instruction
and workflow.stage_contract. Active workflow is execution context, not a raw
stages graph. For a typed newborn with a selected or supplied process, read the
full top-level workflow, separate from draft; do not invent draft.workflow or
assume an untyped newborn already has a graph. Choose a new Task's source through
the explicitly selected project and starter. Follow the canonical
[source-selection procedure](../../../docs/workflows/task-planning.md#выбор-источника-процесса)
for fields, relative paths and source failures.

Before storage setup or recovery, read the current [owners and schema
versions](../../../docs/architecture/storage-lifecycle.md#владельцы-и-версии). Configuration schema
and SQLite version
are distinct; the [limited Task DB 12-to-13
upgrade](../../../docs/architecture/storage-lifecycle.md#ограниченный-переход-task-db-12-в-13) does
not authorize a
general migration.

For WSL project selection and storage layout, read only [Local installation →
Architecture](../../../docs/configuration/wsl-local-delivery.md#архитектура-локальной-установки) and
[Local installation
→ Mutable storage
paths](../../../docs/configuration/wsl-local-delivery.md#пути-project-local-storage).

For configured project operations, follow the versioned
[Stride launcher contract](../../../docs/configuration/stride-launcher.md#настройки-запуска)
and its [independent package version
selection](../../../docs/configuration/stride-launcher.md#установка-и-выбор-версии).
Stride does not create native caller identity or replace the hook-issued session work launcher.
The [historical local
installation](../../../docs/configuration/project-setup.md#локальный-проект-ai-poise)
retains the native session protocols; resolve actual paths through current configuration.
`bootstrap` and `verify` are
operations of AI poise `work`, not separate skills. For ordinary work invoke the session-scoped
`work.sh` supplied by
the native hook explicitly through Bash, keep `messages=[]`, and reuse the same launcher across
calls. Direct
`.venv/bin/poise work` uses a native Codex identity when available; otherwise it requires an
absolute persistent
`POISE_CALLER_BINDING` whose parent already exists. An agent must not substitute a caller-chosen
session value, and a
direct diagnostic call does not establish native event delivery. State placement follows the
selected project
configuration; historical installation examples do not override its current paths.

Treat launcher availability and configured interpreter capability separately. Managed hooks and
launchers execute the interpreter selected by installation settings directly; they do not provide
an alternate task-work runtime or a special recovery protocol. If that interpreter is unavailable,
report or restore the installation through ordinary operator procedures, then repeat the native
event. Do not improvise a system-Python `poise work` invocation as a task-work fallback or claim
that AI poise emitted a recovery command.

When a runtime preflight reports an environment/configuration failure, preserve its
exact interpreter/launcher path and the existing native binding. Follow only
[ordinary operator
restoration](../../../docs/configuration/runtime-hooks.md#действия-оператора-при-потере-среды).
If Poise blocks otherwise executable subject work explicitly authorized by the user,
continue only that subject work manually and record actual changes, checks, WIP and
the incident. Do not repair Poise or invent a replacement harness without a separate
assignment; do not fabricate Poise completion, native delivery or a new identity.

When native hooks and the registry carry different historical installation IDs,
use the existing `runtime-config` `reconcile` operation with the observed live-file
revision, explicit old ID, exact live immutable definition path and full new
definition. Do not append another installation, delete foreign groups, or edit the
hook SQLite registry manually. Reuse the same request after an interrupted write.
Read [installation identity
reconciliation](../../../docs/configuration/runtime-hooks.md#согласование-installation-identity-после-переименования).

Before an unfamiliar operation, follow [packet preparation and refusal
handling](../../../docs/workflows/batch-work.md#подготовка-пакета-и-разбор-отказа): use the actual
configured creation path, exact packet shape and current lifecycle; do not guess API fields. For an
agent-use incident, follow [instruction
clarity](../../../docs/governance/development-rules.md#ошибки-применения-и-точность-инструкций);
detailed corrections belong in canonical docs, with a short skill link.

## Instruction development scope

Treat changes to AI-agent rules, skills and operating instructions as instruction
development. Declare that responsibility when planning the affected stage. Its
`allowed_paths` must include at least:

- `.agents/**`
- `AGENTS.md`
- `**/AGENTS.md`
- The canonical instructions for every affected area; this repository uses `docs/**`.

Check the actual selected project paths and the effective Task StageContract.
Scope permits relevant edits; it does not require unrelated changes. Refine the
plan and criteria of an unstarted stage through their existing owners. Planning
refinement alone does not require restarting the Task; when the effective scope
already covers every planned write, continue within it. Correct a confirmed
frozen-scope gap through the public Task contract owner before writing. Preserve
ownership and independent review gates.

Keep the procedure here and a short obligation in AI-agent rules linking to this
exact section. Follow the canonical Russian [instruction-development
rule](../../../docs/governance/development-rules.md#неизменность-путей-rework-и-граф-этапов).

## Harness refusal and manual continuation

For historical workflows missing `purpose` or `criteria_authority`, follow
[explicit publication-owner
selection](../../../docs/task-workflow-snapshot-migration.md#выбор-владельца-публикации)
and [source-bound
recovery](../../../docs/task-workflow-snapshot-migration.md#публикация-атомарность-и-восстановление).
Use the actual existing project settings when that owner published the workflows;
do not guess stage mappings or create an editor to evade a target mismatch.
Include every newborn identity in the inventory. Use `newborn_draft` only when
`stage_contracts` is absent; full-contract newborns use `newborn_task`. Follow the
[draft resource
contract](../../../docs/task-workflow-snapshot-migration.md#постановки-без-договоров-этапов).
Preserve missing future contracts; migration does not perform task preparation.
Track installed-runtime continuation through the
[open
incident](../../../docs/operations/known-bugs.md#recovery-publication-owner-001--исторические-процессы-блокируют-чтение).

When normal Poise work is blocked, apply
[harness-recovery](../harness-recovery/SKILL.md) and the canonical
[failure and manual bypass
rule](../../../docs/governance/development-rules.md#сбой-harness-и-ручной-обход).
Classify cause and recurrence, log every incident and manual correction, and choose
a verified authorized continuation. Repeated or repeatable problems require one repair
draft, not necessarily code. Configuration defects remain obligations until corrected;
reject failed fixes. Preserve real evidence, independent review and foreign ownership.
The ordinary managed-data prohibition below does not prohibit the narrowly scoped,
audited manual recovery authorized by the canonical rule. Manual work is never a
fabricated native receipt or Task completion.

Users assign agents to roles. Perform both executor and reviewer stages only when the
user assigned both; keep their work and decisions separate. Different workflow roles
do not require different agent/session identities. Preserve actual provenance and claims.
Follow the [canonical role-assignment
rule](../../../docs/governance/development-rules.md#роли-этапов-и-непрерывность-поручения).

## Start or resume work

Before creating or configuring a Poise project, read the canonical
[resource placement and setup procedure](../../../docs/configuration/storage-layout.md).
Use its current-consumer inventory, configuration examples and migration acceptance
criteria. Distinguish supported settings from planned placement changes. Keep
installation-wide workflow-stages/workflow-schemas editors separate from project
stores, and session resources disposable. Migrate needed data and verify every
consumer before retiring old paths; never preserve terminal leftovers in an archive
without a remaining operational purpose. The linked documentation owns the policy.

Tool views are for agent display. Use complete saved responses or native read-only tools for machine
parsing and
decisions; never repeat a mutation just to retrieve output. Internal tool composition receives full
stdout, stderr
and structured fields, without agent-budget clipping. Follow [full
output](../../../docs/workflows/runner.md#полный-вывод-и-preview).

Apply the canonical [ownership
rule](../../../docs/governance/development-rules.md#владение-task-и-worktree):
at most one Task and one worktree per session, independently owned. The workflow snapshot
defines a dependent worktree; release it with its Task but preserve independent ownership.
Acquire the complete set through the public owner, recognizing self-ownership and replacing
old same-kind claims atomically. Never steal an uncertain live claim. Automatic acquisition is
distinct from explicit user-authorized
[after-crash revocation](../../../docs/workflows/crash-ownership-recovery.md): restore into isolated
paths,
confirm stopped writers, use the existing recover_ownership template and preserve
Task history, WIP and pending outcomes. Never fabricate SessionEnd or reuse a revoked caller. Claims
do not change
WIP, cwd or launch roots and do not authorize cleanup or integration. At a role boundary,
the sender saves results, confirms public release, then directly messages the known counterpart;
the recipient acquires through bootstrap before working, under existing Task authorization.

Use one `bootstrap` package to obtain the current task/sprint, stage, workflow snapshot, required
content, worktree,
findings/evidence and available capabilities. For the WSL invocation, read [Local installation →
Start
work](../../../docs/configuration/wsl-local-delivery.md#начало-работы-над-задачей).

Use public `operation: task` actions to prepare a real newborn Task. A real newborn Task has a
permanent identity and history and uses the shared ownership API. With explicit project
`task_planning`, selecting a `goal_type` materializes its registered starter draft. The planner may
reshape task-owned fields and the local workflow before `ready`; template provenance is not runtime
policy. Projects without that setting retain their existing creation path. Use the [planning
API](../../../docs/workflows/task-planning.md#создание-по-типу-цели); never edit managed files or
databases to enable it.
Sprint draft materialization creates each newborn member without a claim, so the planning
session keeps ownership of only its current Task while it edits the draft graph.
`create`, `edit`, `ready`, and `restart` require stable request IDs; preserve exact
replay and reject foreign live ownership. A ready standalone Task becomes available, while a
Sprint member remains newborn until publication. Use Sprint `materialize_tasks` to convert
legacy embedded definitions while preserving partial edits, history, graph aliases, membership,
and source Task traceability. Do not introduce a broad migration or replace direct complete
Task creation.

Apply the [registered-goal
rule](../../../docs/governance/development-rules.md#task-goal-preservation):
keep the registered Task goal unchanged throughout its lifetime, including after
a lawful restart. The API can edit the newborn goal field; that capability does
not grant agent authority to change it. Agree the stage sequence first and design
only the imminent stage immediately before execution, with independent design and
result review. Ordinary refinement of an unstarted stage uses its criteria owner
and does not restart the Task; follow [the shared
contract](../../../docs/workflows/task-execution-contract.md#проектирование-непосредственно-перед-этапом).

Read task_decomposition from the selected project's project-specific routing. Declare every workflow
phase before an
ordinary or integration Task is made ready. Each phase names its
skills and areas; the phase set must exactly match the selected workflow snapshot. Meta and general
skills do not split a
Task; peer narrow responsibilities do. Phase boundaries cannot
hide a cross-responsibility ordinary Task. Across every phase, each area routed to a policy-owned
narrow responsibility must match a declared narrow-skill responsibility; an area whose route is
not owned by a narrow skill does not become narrow through phase placement. Use integration only
when its declaration
names
component inputs, one combined result, integration checks, and allowed paths covering all
phase areas. Treat validation as declaration consistency, not proof that the inventory or
declared scope is factually complete.
`task_decomposition.skills` validates IDs declared in `decomposition.phases[].skills`;
it does not restrict which available relevant skills an agent may read and apply.
Use `documentation` when the work needs it even if that ID is absent from the
project list. Do not report a skill gap or change project configuration solely
because an available skill is absent there. Keep the declared narrow responsibility,
role and Task scope intact.

Every newborn Task `edit` request explicitly supplies both `patch` and `remove`; at least one
is nonempty. Use `remove` in the same optimistic request when a goal-type change makes a saved
draft field invalid. Never encode deletion with null, silently clean the draft, or omit `remove`
as a compatibility path. Preserve exact replay and reject unknown, absent, duplicate, immutable or
patch-conflicting removals. Flexible drafts may remove required fields temporarily; `ready` requires
the complete resolved contract. Frozen edits require authorized restart.

When a saved execution contract makes DoD unattainable, the next stage fails its own DoR, or the
executor concludes from evidence that the available workaround is not adequate to the Task, do not
bypass Poise or manufacture success. Preserve the evidence and agree the exact restart/Task-contract
change with the independent reviewer. The agents decide and execute a restart without another user
decision. An authorized owner uses the public Task action; the executor must not impersonate the
reviewer. For fields outside the frozen restart-revision policy, the reviewer records the exact
approved Task fields in `authorization.revision_fields`. Project/harness rule changes require
separate user authority and are not mutated by Task restart. Preserve Task identity, immutable
history, Sprint membership, worktree/branch and all WIP; reject terminal work, a foreign live owner,
stale version, or a pending external outcome before mutation. Resolve pending uncertainty through
its explicit recovery protocol first. Do not use or recreate the removed Sprint `replace_task`
correction action. Historical relations exist only as opaque revision/audit records, not a current
replacement projection.

Follow [post-restart handoff](../../../docs/workflows/local-handoff.md#передача-после-перезапуска):
restart preserves historical results, proof and provenance; restart alone does not make
proof inapplicable. Apply the existing source/condition/provenance rules. A historical
submission is not new work. Current templates use only the Task's authoritative current submission,
never a latest
historical stage/iteration match. Save partial material through the artifact owner before release.
Without a new current result, preserve actual Task state; with one, transfer that result.
Ready preserves the feedback book, including open findings and previous review decisions;
do not reapply rejected historical corrections or manufacture finding closure.
Use ordinary public release to release a restarted Task, including newborn work; restart itself
retains ownership. Preserve ordinary refusal, material-integrity and exact-replay gates.

Task restart atomically invalidates every mutable current `work-packet identity` for that
Task in the same Unit of Work as the newborn lifecycle reset. This does not rewrite or delete
immutable `submissions`, `task_results`, `evidence`, or Task `history`; failure of invalidation
or any later restart step rolls the whole transaction back. After `edit`/`ready`, save a fresh
result normally even when the restarted route reuses the same stage and iteration. For a current
verified result, an exact packet replay remains idempotent, while different result/artifact input
must follow the domain `PoiseError` rework path and must never surface an internal `NameError`.

When an interrupted invocation leaves `pending=checks`, retry only the exact current submitted
`verify` packet. The runtime may reconcile it without rerunning checks only when every terminal
receipt is present and exactly matches the current stage, iteration, submission, tree, execution
key, invocation, ledger record, and persisted output digests. Treat incomplete, mismatched,
duplicated, unknown, or output-corrupt receipts as unresolved external outcomes and make no Task
or execution mutation.

Public non-newborn `bootstrap` and current-Task `show` projections expose the exact current Task
version as `version`.
The private `_version` name is never part of the public DTO. Taskless responses omit `version`, and
newborn Tasks use
`revision`. Pass this value unchanged as `expected_version` for guarded `restart` or stage-contract
repair; on a version
conflict, refresh through public `bootstrap`/`show` instead of guessing.

Use the installation-owned `recover_missing_worktree` operation only for a nonterminal
verified/accepted Task whose registered worktree was removed. It must prove the saved report
commit is integrated into the configured base and has the exact verified tree before restoring
the saved branch/worktree. Reject a path that is not a registered worktree of the configured
repository. On failure, remove only the worktree and branch created by that invocation; it never
changes Task ownership, lifecycle, result, or handoff. An idle session or the current owner of
that same Task may invoke it; an owner of another Task may not.

If a lawful external workflow publication leaves the goal-config editor head stale, first use
the read-only `goal-config-status-1` diagnostic and establish the provenance of the live file.
Adopt it only through an exact `goal-config-reconcile-1` request with the observed managed and
live revisions, reason and authority. Do not create a fresh editor database to bypass stale
ownership metadata. Follow [Goal config → revision
reconciliation](../../../docs/configuration/goal-config.md#сверка-управляемой-revision-с-live-конфигурацией).

When `bootstrap` explicitly addresses a `completed` or `cancelled` Task, consume the returned
`terminal inspection
snapshot` with its preserved context, content, evidence, and history. Do not expect or create a
current-task binding,
and do not issue a follow-up `show` to recover terminal data. A cancelled Task may legitimately have
no evidence. After
inspection, taskless bootstrap must return `read_only`; only then may null-result verify return
`read_only_verified`.

Do not create a worktree for read-only queries. For repository-changing work, use the worktree
supplied by AI poise and
read the target repository's applicable `AGENTS.md` before edits.

## Stage skills: draft contract and current reporting duty

The [stage-skill
proposal](../../../docs/workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
describes two Task-owned assignments per stage: meta and subject skills, initially
copied from the selected stage template and adjusted by the planner. It is not yet
implemented in Task/template schemas or bootstrap/verify enforcement. Do not send
invented fields or block existing Tasks on absent draft fields. Use actual current
stage assignments; router recommendations do not silently replace them. Report any
missing or insufficient specialist skill in the final answer with its stage, impact
and proposed planner action. Escalate real blockers immediately and preserve gaps
in existing allowed result/handoff sections, without inventing a new result schema.

## Complete the current stage

Submit the stage result and related sections/findings/evidence/artifacts in one logical package. Use
`verify`; do not
hand-edit intermediate result files or the database. Read [Batch work →
Verify](../../../docs/workflows/batch-work.md#verify) and, when files are required, [Batch work →
File
creation](../../../docs/workflows/batch-work.md#создание-файлов).

Choose either documented `verify` input form: the complete `result`/`artifacts`
form or [selected result
submission](../../../docs/workflows/batch-work.md#выбранная-сдача-результата).
For selection, explicitly choose substantive sections, files and metadata decisions
using fresh Task/stage/iteration/version coordinates. The native owner fills the
actual template and computes file digests; it does not choose claims or verdicts.
Each supplied metadata field replaces that whole template field. Use the complete
form when choosing text/template sources or an external absolute file with digest.
Preserve the original admitted request identity and exact packet for a retry;
read the complete saved response and follow its actual continuation. Verification
does not perform advance, role handoff, acceptance or integration on your behalf.

Batch independent reads that are known before the call into one public `show` packet with
multiple `queries`. Give each query a stable unique `id` and correlate the returned items by
that ID. Use the existing `task`, `section`, `content`, `evidence`, `verification_registry`,
`trace` and other supported query kinds; do not create another reader or inspect managed SQLite.
Do not batch a dependent read until its identifier or range is known. One packet is not a
cross-read-model SQL snapshot, and no token saving has been measured or may be claimed. Follow
[Evidence-based review → Batched subject
reads](../../../docs/workflows/evidence-based-review.md#пакетное-чтение-предмета).

When submitting, reading or correcting a current author proof, use the
[canonical proof input](../../../docs/workflows/evidence.md#один-пакет-результата),
[current-byte reading](../../../docs/workflows/evidence.md#чтение-нынешнего-содержимого) and
[same-path correction](../../../docs/workflows/evidence.md#исправление-на-прежнем-пути).
Classify registered acceptance artifacts separately using their lifecycle below.

Let the exact workflow schema decide whether Task creation contains
`executable_obligations`. A schema with a `test_registry` inspection route requires the field,
including an explicit empty list; a schema without it must omit the field and receives an empty
public registry classification without requirement/DoD inference. For repository verification,
an empty `change_surface` represents only a pre-existing baseline guard with no RED and sole
GREEN at the saved route entry, whatever its Task-owned name (for example, `baseline` or
`reproduce`).
Produced-result GREEN methods require a non-empty surface
covered by their stage. Follow [Batch work → Current verification
registry](../../../docs/workflows/batch-work.md#текущий-реестр-методов-проверки).

A guarded registry packet may use `operations: []` only for an actual change to
`executable_obligations`; retained methods must remain valid. Do not invent a method
to reclassify a restarted Task. Use the authorized public restart/edit/ready route:
the Task owner derives the mutation, and a schema without the field requires its
explicit removal from the draft. A fresh empty packet with unchanged classification
is rejected; unchanged ready creates no registry mutation, while accepted exact replay
returns its original receipt. Preserve historical evidence and conditional old/new
classification audit fields. Follow [Batch work → Registry after Task
restart](../../../docs/workflows/batch-work.md#реестр-после-перезапуска-task).

When a current observation command has become stale, do not recreate the Task or edit its
database. Read the current registry with one `show` query of kind `verification_registry`, then
submit one guarded `method_additions` change from the current `observe` stage. Replace only a
method owned by that stage's `evidence_plan.subject_methods`; provide the exact current revision,
a new idempotent request ID and unchanged stages, `evidence_kind`, `covers` and
`executable_obligations`. Add/remove/reschedule and guard or classification changes still require
a `test_registry` stage. Preserve the returned audit receipt; a replay must return its original
identity without another event. Follow [Evidence → stale observation
replacement](../../../docs/workflows/evidence.md#замена-устаревшего-метода-наблюдения).

Use native coding/IDE tools for source changes. AI poise owns task state, evidence registration,
execution receipts, Git
lifecycle boundaries and managed artifacts. Follow the canonical [executor/reviewer stage
policy](../../../docs/governance/development-rules.md#роли-этапов-и-непрерывность-поручения). A user
instruction to
start a Task authorizes its executor and reviewer to continue ordinary execution, review and
remediation through AI
poise gates without a new user command for each stage or review. Honor an explicit stage-only
assignment or other user
limit. Continue stages of your own role; at a role boundary follow the handoff procedure below. Do
not bypass gates or
act as your own independent reviewer. Stop for a real blocker, a new required decision, or
separately controlled
acceptance/publication/integration.

Use public `advance` with a stable `request_id` and exact Task ID. Supply an exact
`target_stage` to stop on entry, or omit it for maximum progression. For ordinary new work, replay
that exact request after completing and verifying each current-role
stage, after a lawful correction or restart, and after the receiving session bootstraps the
released Task in its existing working area. Treat
`progression_work_required`, `role_handoff_required` and `user_acceptance_required` as pauses, not
success. At a role
boundary, include the active progression identity in the direct handoff message. Never change its
target under the same
request ID or use it to cross gates, perform stage work, send the handoff message, record user
acceptance, or infer
publication authority. A publish stage opens only through the separately authorized public
acceptance path.

Continue the exact current verified result through its proved ownership history after an
authorized ordinary release/acquire or authenticated historical ownership events. A lawful
release/acquire may continue the next role with the same actor
when the user assigned both roles; changing identity is not required.
Keep the same progression intent and all source, ownership, unknown-outcome and review gates.
Follow the canonical [verified-result
continuation](../../../docs/workflows/batch-work.md#продолжение-проверенного-результата-после-освобождения);
this does not replace the paired handoff/message procedure or grant acceptance/publication
authority.

Poise isolates the temporary Git index for every repository snapshot invocation and cleans only that
invocation's owned
directory. Never delete `snapshot.index.lock` manually or remove a sibling snapshot directory:
preserve a genuine Git
conflict diagnostic and let the owning lifecycle recover it. See the canonical [snapshot index
contract](../../../docs/workflows/batch-work.md#изоляция-временного-git-index).

Never run the full test suite during task work, including at a delivery boundary. Run only narrow
task-specific checks
and the maintained bounded `tests/smoke.sh`; never register unfiltered repository-wide test
discovery as a task method.

Before project setup, changing `git.base_ref`, or integration, read the canonical
[target branch
selection](../../../docs/configuration/project-setup.md#выбор-ветки-основного-checkout-для-интеграции).
For final result integration, keep the accepted commit immutable. The public finisher advances the
existing task branch
in its existing task worktree; all updates from the current configured target branch, merges,
conflict resolution, and checks happen there,
with no separate integration branch or worktree. If it returns a conflict, pause automation for
agent resolution in that
task worktree, then resume checks. The finisher rechecks the configured target under the shared
target lock and repeats update,
resolution, and checks on drift. It publishes only through `git merge --ff-only <task-branch>` in
the main checkout and
never updates the target ref directly or by force.

Public `integrate` requires separate explicit `authorization` and full `commit_message`.
Keep the original permission and message unchanged across retries, conflicts, target drift,
and crash resume. The configured `git.commit_pattern` matches the complete message before
Git or saved-run loading; do not substitute permission, defaults, or a shortened subject.
The shared candidate-commit owner preserves the full message with Git's documented final-LF
framing. Authors separately check product meaning and 50/70 writing rules; these have no
new automatic validator. Follow the exact [message contract and packet
examples](../../../docs/workflows/batch-work.md#разрешение-и-сообщение-интеграционного-коммита).
Retained integration history without a recorded message cannot automatically continue,
even if a new request supplies one. Preserve history and recovery data; do not backfill
from authorization, edit managed state, or change request identity to bypass refusal.
Task inspection remains available but does not expose the full saved integration packet;
the integration projection also requires a recorded message. Follow [missing-message recovery
limits](../../../docs/workflows/batch-work.md#сохранённый-запрос-без-сообщения-коммита).

Before the first product write, distinguish a real Task from an explicitly authorized direct
correction without one.
The public `integrate` operation requires a completed Task and saved final commit; it cannot accept
a taskless candidate
retroactively. A reviewed taskless correction with explicit user authority may use the separate
manual local delivery
route, preserving exact commit/check identity, target and foreign WIP.
**Checkpoint applicability: when working in the cloud.** Creation and verification remain required
in that environment;
local work requires them only on a separate explicit user request. Follow the canonical
[route
decision](../../../docs/governance/development-rules.md#прямое-исправление-без-task-выбор-и-поставка)
and
[pre-ready
procedure](../../../docs/workflows/pilot-task-preflight.md#выбор-маршрута-до-первой-записи). Never
manufacture
Task stages, acceptance or receipts for earlier taskless work.

Never execute `git push`. This prohibition is absolute for every agent, AI poise publication
handler, Git adapter, and
configured verification, action, tokenizer, or capability-probe runner; user publication authority
does not waive it.
Reject `push_required=true` before any Git command, keep `push_required=false` remote-free, and
direct completed Task
results to the public local `integrate` operation and its exact `git merge --ff-only` publication.

Never use the main checkout or foreign WIP for preparation or conflict resolution, and never
`stash`, `reset`,
`restore`, `checkout`, `clean`, stage, commit, or delete their state. A dirty or unfinished main
checkout may block
`merge --ff-only`; require recorded before/after proof that its full state is unchanged. After
confirmed publication,
the same lifecycle removes only the task worktree, task branch, and registered task-scoped temporary
backups. Preserve
pre-existing, foreign, durable operator, deliverable, and unfinished-recovery backups. Resume
persisted incomplete
phases idempotently after a crash, using current installed AI poise source. Read the canonical
[finish and integration
rules](../../../docs/governance/development-rules.md#интеграция-завершённого-результата).

## Sprint work

Published Sprint discovery includes preparing members in `counts.newborn` and marks them
blocked with `task_not_ready`; unrelated ready branches remain eligible. Read-only discovery
does not grant execution readiness or alter ownership. See the [Sprint
contract](../../../docs/workflows/sprints.md#обзор-опубликованного-спринта).

Plan only one connected dependency chain per Sprint. Keep independent Tasks standalone;
neither a shared topic nor execution priority creates a dependency. When splitting a long
Sprint, copy required prerequisite Tasks or chains as local conditional duplicates with new
IDs and local edges. Do not search similar Tasks or manually maintain a reciprocal family
list. Task has two axes: ordinary/duplicate, then parent/child for a duplicate. A root
with children is itself a parent duplicate. One shared library method resolves the original
parent, lists all children, then batch-reads the parent's and all children's stages, workflows,
statuses and session owners in a consistent DB read. Only parent resolution differs by role.
Context and stage admission reuse this method; no per-task lookup loop or copied traversal.
The harness derives reciprocal family views from a canonical parent relation.
For each noncancelled member, d is the shortest directed transition count from its current
stage to positive termination, computed equivalently by reverse traversal from positive
terminals. Success is d=0; cancellation is not success. Rework changes current distance;
stage names, array indexes, branch position and past maximum progress do not rank Tasks.
Include the current Task in d_min. Ordinary repeated implementation MUST be rejected when
d(current)>d_min,
even by one transition. Equality is necessary but not sufficient: when two or more nearest
Tasks have sessions, refuse start until agents agree one executor and that executor explicitly
supplies force_duplicate_start=true for this request. Count the requesting session prospectively
for an unclaimed current Task, without first persisting its claim; do not double-count on resume.
The optional bootstrap/advance flag clears only this tied-session refusal, never distance, ownership
or stage guards.
Return only all other relatives at d_min, with Task/Sprint IDs,
stage, distance, status, iteration and owning session IDs. Do not include intermediate
leaders or tied-but-lagging peers. Equal distances on different stages are ties too.
Multiple sessions at equal minimum distance require agreement and the flag even on initial
stages; unowned initial equality alone is not a collision. A lagging initial stage is blocked.
Every request, including a flagged retry, rereads the family; never auto-add or persist the flag.
Check on
acquisition/resume and every
actual stage start before execution effects; a context warning is not enforcement.
Invalid or unavailable distance is not permission. Do not seize foreign ownership or WIP.
Completed family results still require main-base integration and local availability;
reuse must not bypass a denied ordinary-stage start or forge terminal progress/acceptance.
Use work/reuse with explicit task_id, source_task_id, request_id and current expected_version for
the first request. It
verifies the matching accepted family contract, base_ref integration and own local registered
checks; only a separately
authorized accept completes it. Replay the original packet, including its original expected_version,
without inventing a
stage result. Before acceptance replay validates local inputs; after completion it returns the
historical accepted
receipt without reacquiring a Task/worktree. Ordinary release/bootstrap resumes the same working
area and validates saved reuse provenance.
Reuse automatically delivers the accepted permanent Task/Sprint artifact set into local owner roots.
It preserves bytes,
assigns local identities, checks the active delivery obligations and refuses conflicting paths.
Replay preserves identical
files; accept rechecks delivery. Do not copy files/receipts manually or treat foreign
runtime-session artifacts as permanent
results. Optional empty artifact sets are valid only when their actual counts and delivery
obligations allow them.
See [automatic family
check](../../../docs/workflows/sprints.md#задача-спринта-дубль-автоматическая-проверка-семейства).
Create a duplicate with work/task action duplicate: request_id, new task_id, parent_id,
and an existing destination draft sprint_id. It starts as an unowned newborn and follows
ordinary ready/publication. The shared reader and start gate are implemented. Do not
invent fields, automatically set force_duplicate_start or perform manual similarity searches.
Follow the existing
[dependency closure and local
duplicates](../../../docs/workflows/sprints.md#замкнутость-зависимостей-и-локальные-дубли).
Do not claim graph connectivity is automatically enforced merely because DAG validation exists.

Bootstrap the sprint to get the eligible set instead of calculating dependencies manually.
`verified` is not
`completed`; predecessor completion follows the task's acceptance policy. Read [Sprint API → Work
selection](../../../docs/workflows/sprints.md#выбор-работы-и-один-пакет-контекста) and [Sprint API →
Dependency
kinds](../../../docs/workflows/sprints.md#два-явных-вида-зависимостей).

Treat result dependencies as readiness plus `result_provenance`, not Git ancestry. Every new
successor worktree starts
from the current configured base ref; AI poise does not use or merge predecessor result commits as
its branch base.

Inter-task dependencies exist only inside one Sprint; a standalone Task or a member of another
Sprint cannot be an endpoint. Use draft `adopt_tasks` atomically only for Tasks with
`status=available, claimed_by=null, worktree=null, pending=null, last_report=null, and attempts=0`.
Use published `extract_tasks` only for the same eligible member with no incoming or outgoing
dependency.
Identity, immutable history, goal, contract, and readiness are preserved. A
successful conversion changes membership, graph, revision, and the request receipt together;
rejection changes none of them, and a retry after rejection is not a replay. Draft
`remove_tasks` remains the pre-publication correction route, and draft cancellation atomically
detaches both newborn and adopted Tasks.

## Capabilities

Treat configured inventory and actually probed capability as different facts. Use only capabilities
reported for the
current project/worktree. For the current probe semantics, read [Runtime hooks → Capability
checks](../../../docs/configuration/runtime-hooks.md#проверки-capabilities).

## Task DB backups

Discover the public backup surface with `poise backup help`. Use `poise backup list --config
PROJECT_JSON`, `poise
backup create --config PROJECT_JSON`, and `poise backup restore --config PROJECT_JSON BACKUP_NAME`;
do not substitute
manual filesystem copies or database replacement. Create and restore require an exclusive operator
window in which no
agent or workflow writes the Task DB. Read the exact [exclusive operator
window](../../../docs/task-db-backups.md#обязательное-условие) and [Task DB restore
contract](../../../docs/task-db-backups.md#восстановление). Task-only backup is not a full
checkpoint: preserve the
other databases, Git and materials under the [full backup
boundary](../../../docs/architecture/storage-lifecycle.md#резервное-копирование-и-восстановление).

## Handoff and transfer

User Task start authorizes routine reviewer work as well as execution; no separate command is needed
for each review. For a ready stage result use public
[`handoff`](../../../docs/workflows/batch-work.md#передача-результата-одной-командой)
with the complete current result template, original artifact intents, explicit Task ID,
stable handoff request ID and reason. Omit target_stage for maximum progression, or
provide the exact intended stage. The composition verifies, advances through existing
gates and performs ordinary release; it neither does stage work nor messages the receiver.
Read the full verification/progression/release outcomes. Preserve the returned active
progression identity for the receiver's exact advance; it is distinct from the handoff ID.
For incomplete materials, save them truthfully through their owners and use separate
ordinary release without fabricating a completed result or GREEN. The receiver acquires
the same Task through public bootstrap before mutation. Read [Local handoff → Role
transfer](../../../docs/workflows/local-handoff.md#передача-между-исполнителем-и-ревьюером).

Confirm actual release before messaging and stop Task changes after release. A blocked
response or release:null does not establish retained ownership: release may have committed
before its acknowledgement was lost, and the receiver may already own the Task. Establish
the real outcome through ownership/original receipt and retry the exact original handoff;
its recovery preserves the receiver's claim. Do not blindly issue a new release or reacquire.
A known refusal before the release effect leaves ownership unchanged. An unknown check
outcome still requires its existing recovery owner; an exact retry is not permission to
rerun an unknown external command. A chat message is not release, independent review,
Task acceptance or publication. No local archive, bundle or transport is created.

When the Task's counterpart executor/reviewer is known, apply [Direct role
handoff](../../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим)
in both directions:

1. Submit the ready result, evidence and findings through handoff, or preserve incomplete
   materials and use ordinary release. Confirm the actual ownership-release outcome;
   resolve a lost acknowledgement through the original request before assuming a claim.
2. Stop Task/worktree changes, then use the available direct-message tool to notify that known
   counterpart. Include
   project/Task ID, stage and receiving role, result/findings and ownership-release receipt links,
   next action
   and any limits of
   the user's Task-start instruction. Do not ask the user to relay the message or authorize each
   ordinary review.
3. After successful notification, check your own pending assignments and immediately continue
   actionable work or the
   authorized stage/Task chain in the same turn. Keep received assignments in the working plan until
   completed,
   cancelled or explicitly transferred; preserve them and their blockers across checkpoints and
   compaction.
   Acknowledging a message does not discharge it. New messages add work unless they explicitly
   change the assignment. Do
   not poll the counterpart's status, read its conversation for progress or schedule background
   monitoring.
4. The recipient must acquire through `bootstrap` and check current state before working. A message
   neither transfers
   ownership nor substitutes for a review decision. Keep messages actionable, not repetitive status
   chatter.

If the recipient or messaging tool is unavailable, report the saved handoff to the user; do not
contact unrelated
agents. If handoff fails, do not announce a completed transfer. If notification fails after release,
retry the
notification against the same saved result or report the blocker; do not silently reclaim or repeat
the work. Repeated
messages require checking current Poise state, not replaying a finished stage. Stop for an AI poise
runtime blocker or a
decision outside the existing authorization, not for a routine executor/reviewer boundary.

For multiple incoming review handoffs, follow the linked direct-handoff rule: if idle, acquire and
start immediately;
otherwise finish the review already started, save, release and notify, then take the next
already-received actionable
Task in the same turn. A new handoff does not interrupt the current review or claim a second Task.
For example, after
reviewing 0082, start pending 0076 immediately rather than ending with "0076 is next".

Before a final answer, account for every received assignment and continue any authorized actionable
work. End the turn
only when none remains; preserve the next action and concrete blocker for each pending assignment. A
blocker on one Task
does not stop other assigned work when ownership permits switching. Complete your own
already-started operations. Never
rely on another message to restart work that has already been assigned; duplicate notifications do
not require repeating
completed work.

Scoped export/import is removed. Continue the same Task and working area through ordinary
release/bootstrap; do not merge task databases or copy work materials between participants.
See [removed transport](../../../docs/workflows/transfer.md#пакетный-api).

## Observed usage

Record only real observed user-message/token events and preserve source/coverage. Do not estimate
unavailable usage. For
metric semantics, read [Accounting → Observed
telemetry](../../../docs/operations/accounting.md#пакет-наблюдённой-телеметрии).

## Documentation references

When an operational rule is owned by canonical documentation, link to and read the smallest
normative section that is
sufficient for that rule. Do not require an agent to load an entire large document when one section
is authoritative.

## Route semantics

Route definitions contain an explicit `entry`; stage outcomes, targets and rework targets own
transition semantics.
Preserve visits and transitions only for history, identity and audit. Never impose an execution
limit through route
counts, depth, watchdogs, timeouts or recursion bounds. A finite graph-reachability check is
structural validation, not
an execution budget.

Do not request rework while a pending resolution still requires independent inspection. Follow the
exact inspection
stage reported by Poise, decide every pending resolution there, and only then retry an authorized
rework target. The
rejection is state-preserving; do not create a workaround Task or edit the Task DB. Use
`recover_empty_rework` only for
a legacy task already stranded by the former defect. Recovery requires an unowned Task and an
ownership-only event
suffix, not a synthetic handoff. A cleaned worktree is recoverable only when Poise proves the saved
commit is integrated
into the configured base and has the exact verified tree; never recreate that worktree manually.
Read [Batch work →
rework with pending
resolutions](../../../docs/workflows/batch-work.md#rework-при-нерассмотренных-исправлениях) (Task
0063 RD-013) and [empty rework
recovery](../../../docs/workflows/batch-work.md#восстановление-ошибочно-открытой-пустой-rework-итерации)
(Task 0063
RD-008).


## File-scoped instruction preparation

Use the existing skills operation prepare with explicit project manifest,
checkout, policy, catalogue, instruction root, concrete paths and previous.
Read required material bodies and apply them within their scopes; distinguish
recommendations. Refresh after input or instruction changes with the complete
previous response, not a clipped view. Ready reports instruction availability,
not model cognition, tests or Task acceptance. This general operation does not
automatically replace native bootstrap or the existing AI-poise selector.
Configuration and exact protocol belong to
[the canonical operator
contract](../../../docs/configuration/development-routing.md#подготовка-инструкций-по-файлам).

## Inputs for migrated skills

The project-local `.agents/skills/` tree supplies discoverable instruction files, not a second
managed routing policy.
Use the selected Poise project and actual Task/stage inputs. Read skill classes and area
declarations from the project's
existing `task_decomposition`; resolve other project settings through their current owner.
Target-worktree files remain
read-only sources of facts for this configuration boundary.

## Procedure for migrated skill selection

For current Task discipline load [exec-task](../exec-task/SKILL.md); for formulating one Task load
[task-design](../task-design/SKILL.md); for Sprint graphs load
[sprint-design](../sprint-design/SKILL.md). Keep the exact packets, launcher, lifecycle and evidence
semantics in this
skill and its canonical documentation. Load other catalogue skills only when relevant to the current
stage and real
project stack, not as a blanket catalogue read.

The current decomposition validator checks declarations; it does not by itself return a complete
ordered area/stage
skill-and-input packet. Do not label instruction discovery as implemented automatic routing,
synthesize undocumented
profile fields, or place an AI poise config in the target repository. An absent required
routing/aggregation capability
is a concrete dependency for the owning Poise work, not a reason to invent a parallel router.

## Output boundary for migrated skills

Persist the current skill's result through the existing Task/content/evidence/artifact owners.
After saving, use ordinary public release and directly notify the counterpart for bootstrap of
the same Task and existing working area. Saving remains distinct from acceptance.
Universal phase recovery remains
capability-dependent. The implemented
[cross-project next
overview](../../../docs/configuration/project-setup.md#доступные-задачи-во-всех-проектах) is
read-only: it neither reserves nor starts a Task. Use the normal owning bootstrap/start path for an
already-authorized
selection, with current-state validation.

## Accidental cancellation recovery

Use the public `task` action `recover_cancelled` only with real explicit user
permission, exact `task_id`/`expected_version`, a stable `request_id`, nonempty
`reason` and `authorization`. This restores the exact prior unfinished state;
it is not `restart`, does not acquire a claim and never edits Git/WIP. Preserve
its audit and use ordinary bootstrap afterwards. Completed/integrated work,
foreign/ambiguous ownership, pending external effects or cleanup with a chosen
commit disposition must not be bypassed. An old cancellation without a precise
recovery point is not guessed. Do not issue direct Task DB edits.
Read [the exact packet and
guards](../../../docs/workflows/batch-work.md#восстановление-ошибочно-отменённой-task).


### Registered artifacts after owner-root migration

Updated: 2026-09-17. With explicit user authorization, recover one released Task's
registered artifact collection through `recover_artifacts` from a taskless caller.
Use observed version, existing IDs and exact old owner roots; do not edit registry
rows, claim another Task or relax path/digest validation to get past this defect.
Current configuration determines destinations. Missing historical bundle bytes
must come from a real backup; a receipt is not a substitute. Read the
[public root-recovery
contract](../../../docs/workflows/batch-work.md#восстановление-зарегистрированных-артефактов).

### Editable acceptance drafts

Before registering an acceptance artifact that may need same-path review/rework, or
recovering a prematurely frozen artifact, read the
[canonical draft
lifecycle](../../../docs/workflows/batch-work.md#редактируемые-приёмочные-черновики).
For a current author proof, use the separate proof contract above.

### Ambiguous legacy ownership

Use `show` with `kind: ownership_conflicts` and then the explicitly authorized
`recover_ownership` decision for one exact connected component. Copy its actual
snapshot and Task/session identities. Keep existing values or release to null;
never infer Task ownership from a worktree binding, invent a claimant, or select
between live/uncertain owners. In this legacy reconciliation mode only authoritative DEAD permits
foreign release;
explicit self-release is supported. Unrelated Tasks remain usable while v12
uniqueness installation is pending. Repair never mutates Git/WIP and exact replay
never repeats the mutation. Read the [public recovery
contract](../../../docs/workflows/batch-work.md#восстановление-неоднозначного-владения).

A Git `publish` stage with `push_required=false` records local-only publication
after accepted clear inspection. Submit the exact target ref, expected local
commit and explicit authorization. No remote is required or contacted; the
receipt does not move the target and is not integration. Resume the same intent
or use lawful rework when blocked; see [local
publication](../../../docs/workflows/batch-work.md#локальная-фиксация-публикации).


Native caller isolation: a session launcher is not caller identity. Use only the launcher
for the host-observed CODEX_THREAD_ID/CODEX_SESSION_ID; both must agree when present.
Never set these values to impersonate a stored binding or combine native identity with
POISE_CALLER_BINDING. Configured agent_id is a profile, not a separate actor. A child with
parent-only signals must not mutate the parent's Task. For a handoff to another assigned agent, the
sender uses ordinary public release;
the receiver uses its own native launcher and bootstraps the exact Task. If the user assigned
both roles to one agent, the same actor may resume after release. Preserve WIP and diagnose
missing/conflicting signals instead of renaming
roles, overriding native identity or editing Task DB to get past the guard.

## Requirements provenance

Use the project Requirements batch API and show the full System → Application → Task
texts and statuses before agreement. Never infer acceptance from a ready plan or IDs.
Publish the exact agreed snapshot through the existing Task/Sprint owner; preserve
historical snapshots on replay/restart. Follow [agreement and
publication](../../../docs/workflows/requirements-registry.md#декларативный-api-и-согласование)
and [immutable
history](../../../docs/workflows/requirements-registry.md#публикация-и-исторический-снимок).

## Local recovery after imported duplicate results

An accepted relative is an input, not proof that this branch satisfies its requirements.
After importing the exact accepted commit, a failed/stale reuse candidate must not trap
the local Task behind family_ahead. Use the existing authorized task/restart, optional
contract edit, ready and normal bootstrap/verify/accept in the preserved worktree.
The Task owner records validated local_repair provenance; this local correction is
not governed by the relative's distance and never requires another repair Task.
Keep ordinary ownership, stage requirements and local Sprint dependencies. Do not
use force_duplicate_start as a repair bypass, fabricate success or remove family links.
A verifier may explicitly revise a defective check through the existing authorized
contract operations; never weaken checks automatically. Unknown checks require effects
inspection/stoppage before authorized restart archives their attempt without replay.
See [the recovery
contract](../../../docs/workflows/sprints.md#локальная-доработка-через-общий-restart).

A newborn Task has no route entry before goal_type selection; selecting the type
materializes its configured draft, without starting execution before ready.

## Terminal inspection

Select a completed or cancelled Task by its canonical ID to obtain the
terminal inspection snapshot. The result is read_only_verified where applicable;
it does not reclaim the task, restore its worktree or repeat acceptance.
Use the returned persisted results for read-only history, not the released
current-session binding.

## Accepted-stage replay after restart

Commit owned WIP in the Task worktree. Replay preserves its current commit/tree and recovery ref;
check reached stages there without historical checkout or reuse of an old PASS as fresh proof.
Keep current proof-contract compatibility and mandatory file integrity; exact completed request
replay returns its saved result. Unknown external outcomes never authorize another execution.
New work retains independent review. Follow the [canonical replay
contract](../../../docs/workflows/batch-work.md#автоматическая-промотка-после-перезапуска).

## Compact Task recovery

Continue one Task in its existing working area through ordinary release/bootstrap.
Scoped export/import, participant transport and automatic bundle creation are removed.
Preserve current source/proof identity, explicit companion configurations when declared,
and unknown outcomes through their existing owners. Separate state relocation, library
distribution and explicitly authorized backups retain their own contracts.
See [same-area continuation](../../../docs/workflows/local-handoff.md#пакет).

## Permanent delivery and terminal material retirement

Use [delivery/agree and
delivery/settle](../../../docs/workflows/terminal-materials.md#публичный-договор-поставки)
with explicit permanent outputs or authorized no_result before terminal handling. Task-root is
disposable working
storage, including raw evidence, temporary artifacts and checkpoints. Runtime settlement after
terminal operations
retires it only after confirmed outputs, integration/resource closure and all required consumers;
missing agreements
are never inferred. Close blockers through their owners and retry the original operation. Read
delivery state through
show/task_delivery without a claim. Do not manually delete managed material or create a hidden
archive.

An unfinished retirement re-observes all permanent confirmations without rereading removed sources,
republishing or
repairing external bytes. A completed replay is historical: no fresh checks, claims or root
resurrection. Reuse of a
retired artifact requires its unique agreed permanent bytes through the existing resolver. Prepared
export and exact
completed-export cleanup continue from their original request/package, not a newly captured source.

Deliberate ordinary-directory replacement at the configured Task path is allowed; saved device/inode
is initial audit,
not admission. Keep actual owner/path/type/link/foreign-material checks. Follow the
[operator
scope](../../../docs/workflows/terminal-materials.md#владение-каталогом-и-намеренная-замена).

For explicit project selection through the supplied native entry, follow the short
[setup instruction](../../references/project-storage-setup.md#session-launcher-project-selection).
Use one exact selector and the selected project's own message contract.

## Working-result placement

Use the separate `placement` CLI for explicit working-result declarations; its
JSON stdin is not a Work envelope. Select the project, registered checkout and
operator-owned policy explicitly with the authentic caller. General placement
does not require a Task; enabled Task/Sprint rules obtain their current owners.
Prepare anticipated outputs, including tool outputs, and check explicit paths
against current policy. This does not change content-edit authority, immutable
artifact ownership, hooks or cleanup. Follow the
[operator
prerequisites](../../../docs/configuration/result-placement.md#подготовка-проекта-и-вызов),
[packet contract](../../../docs/configuration/result-placement.md#пакет-prepare-и-check)
and [failure/retry contract](../../../docs/configuration/result-placement.md#отказы-и-повтор).

## General project-file reading

Use installed `poise content-read` for complete or bounded project-file content without a Task.
Select absolute config, registered checkout and operator-owned policy, plus explicit `--format
json` or `--format text`; preserve genuine caller identity and native admission. Send the reader
operation packet directly, not a Work envelope. Follow the canonical
[setup and
examples](../../../docs/configuration/project-file-reading.md#подготовка-политики-и-примеры)
and [episode
recovery](../../../docs/workflows/file-intake-lifecycle.md#завершение-и-восстановление).
Keep complete machine JSON/frames and saved responses; a registered read is not acknowledgement
or model-memory proof. New context transitions accept only reason `manual`; replay preserves the
saved transition generation without rolling back live state. The specialized acknowledged source
reader retains its existing owner. This API does not intercept arbitrary tools or automatically
reset general context after compaction. The separately configured native
[general work context](../../../docs/workflows/context-recovery.md#общая-механическая-сводка)
shares this reading generation; it does not turn explicit reader requests into native events.


### Ordinary command reads and shared coverage

Use the explicitly installed Bash PreToolUse/PostToolUse adapter only through its genuine
host events. Keep the settings → reader profile → command policy and immutable authority
links from the [operator
setup](../../../docs/configuration/project-file-reading.md#подключение-учёта-обычных-команд).
The same F04 episode/ledger serves direct reads and supported ordinary commands without a Task.
Use [coverage](../../../docs/configuration/project-file-reading.md#сводка-покрытия) for bounded
metadata, and select needed ranges using the actual reason/action. A pending Pre is not
confirmed coverage; a Post warning cannot undo an executed command. Preserve the
[proved cwd and reader
limits](../../../docs/configuration/project-file-reading.md#поддержанные-запросы-и-рабочий-каталог)
and the [existing recovery
owner](../../../docs/workflows/file-intake-lifecycle.md#ожидание-и-завершение-обычного-чтения).
Do not infer host delivery, model memory, acknowledgements or automatic compaction handling
from recorded metadata. This guidance grants no Task acceptance, publication or integration.

### Automatic mechanical work context

Use the genuine native `Work context: ` card only as current scoped metadata. The optional
`general_context` setup and complete inputs belong to the
[operator
guide](../../../docs/configuration/runtime-hooks.md#общая-сводка-после-сокращения-контекста).
The card can be taskless. Disabled work/check owners do not query Task state; a completed check
means process/capture completion, not test success or acceptance. Follow each current read or
refresh action. Resolve skill addresses against the configured instruction root, which may be
outside the checkout; use ProjectInstructions for full instruction text. Safe checkpoint metadata
are not its full `previous` response.

Native Pre/PostCompact and compact Start share one general-reading transition. Explicit
`advance_context` still accepts only `manual`; do not fabricate hook events to force a transition.
A completed delivery retry replays its saved output; a new genuine resume observes current facts.
Keep partial/missing/used states and omissions truthful. The specialized acknowledged SourceReader
and explicit `restore_context` retain their own owners and generation. Clear affects the owned
checkpoint; confirmed End plus the existing reader recovery owner retires its temporary resource.
No card or receipt establishes remembered text, live-host delivery or Task readiness.
