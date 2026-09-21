# Working on AI poise

A direct user instruction may temporarily override a project process rule. Request an exception only when correct
completion is otherwise impossible; explain the rule, the blocked problem, the risks, the scope and when the exception
expires. Platform safety constraints and storage integrity are not project rules that this mechanism can disable.

## Purpose and outcomes

AI poise is a harness that helps agents work declaratively without needing to understand or manually service the implementation details of the tooling. Reduce the model tokens spent administering tasks. Keep bookkeeping, repeated mechanical actions and unnecessary tool calls out of the agent's work so that its effort goes into sound engineering decisions, useful code and clear documentation. Evaluate every proposed tooling change against this rule: prefer interfaces that remove mechanical work and implementation knowledge while preserving the agent's substantive engineering choices. A template, router or recommendation is assistance, not a hidden policy that forces a valid task to fit the starter shape.

Before any work, including a request without a formal task, establish the goal, the requirements for the result, the definition of done and how completion will be demonstrated. Keep this preparation proportional to the request. For Task planning, treat a selected template as materialized starter data: the planner may reshape the task-owned draft before `ready`. `ready` freezes the resolved Task contract and its restart-revision authority; runtime enforces that resolved contract, not the original template.

Use test-driven development for executable behaviour changes: design the checks and write the tests before implementing
the behaviour. Review the tests, implement the change, run the checks, and inspect the code and subsequent fixes. Do not
present self-review as independent review.

Turn every accepted agreement into product documentation. Maintain the [known-bugs
register](docs/operations/known-bugs.md#ведение-реестра): remove a resolved active entry in the same verified fix; keep
history in Git/evidence, and never call an accepted risk fixed. This does not require creating a Task for an explicitly
authorized small direct correction. Treat preview only as display: use full output for machine parsing and decisions via
existing saved files or read-only tools; follow [full output](docs/workflows/runner.md#полный-вывод-и-preview). Make DDD
decisions enforceable development rules and keep responsibility boundaries documented. Do not leave authoritative
decisions only in conversation history.
For changed documentation or rule targets, follow [local links and exact
headings](docs/workflows/documentation-checks.md#проверка-ссылок-и-точных-заголовков), including inbound links to
changed/deleted pages. Use the existing checker; do not create a competing documentation scanner.


Deliver the working path rather than delaying it for speculative combinations of conditions. Handle the ordinary routes
and failures implied by the rules; resolve uncertain edge cases using actual usage and measurements. State current
limits honestly.

## Start and finish work

Work from this AI poise repository and explicitly select the configured project. Target codebases have their own
applicable `AGENTS.md` files; read those before working on them. Use the [AI poise skill](.agents/skills/poise/SKILL.md)
for task work and the [development skill](.agents/skills/poise-development/SKILL.md) when changing AI poise itself. The
[source rules](src/AGENTS.md) govern its implementation.

Use the Task process' explicit worktree policy. When a dedicated Git worktree is required, place it with the
repository/codebase being changed rather than under AI poise mutable state; when it is not required, work in that
project's configured repository checkout. See [worktree
placement](docs/governance/development-rules.md#размещение-task-worktree). Git supplies changed files; the agent does
not register them manually.

One session works on one task. Stage ownership follows the canonical [executor/reviewer policy](docs/governance/development-rules.md#роли-этапов-и-непрерывность-поручения). A user-started Task authorizes its executor and reviewer to continue their respective stages, including checks and remediation, without a new command at each stage or review; honor explicit stage-only or other user limits. Verify each stage and use the [direct role handoff](docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим) at role boundaries. If an executor concludes that the frozen requirements/rules make success impossible or the available workaround is not adequate to the Task, preserve evidence and propose a restart plus the exact requested changes to the reviewer or user instead of bypassing the harness. The reviewer/user decides the restart and Task-contract revision; a reviewer escalates proposed project/harness rule changes to the user, whose approval is required. Stop for a real blocker, a new required user decision, or separately controlled acceptance/publication/integration. Before changing tasks, finish, use the public handoff to release ownership, or safely discard the current work. A read-only query about another task does not switch ownership.

Use `poise project` or `poise project-init` with an explicitly selected template to prepare a new project; do not
hand-edit its managed configuration. See [project setup](docs/configuration/project-setup.md).

Use the existing declarative batch tools for managed configuration, task/sprint data and artifacts; do not edit their
working files or database directly. Native coding tools remain appropriate for source code, tests and target
documentation. Use the current user-authorized scope and applicable canonical documentation; historical plans do not
grant ongoing authorization. Report implemented and tested capabilities separately from planned ones.

When an authorized external publication makes a goal-config editor head stale, use the public `goal-config-status-1` and
exact `goal-config-reconcile-1` protocol documented in [goal
configuration](docs/configuration/goal-config.md#сверка-управляемой-revision-с-live-конфигурацией). Do not create a
fresh editor database to bypass the managed head.

Count observed user messages without inventing missing messages or token usage. Preserve their source and coverage.
Report AI poise incidents even when recovery succeeded; ordinary test failures are work results, not automatically AI
poise defects.

Before every completed-task boundary or risky transition, create and round-trip a portable checkpoint; send the archive
and readable text transport to Gmail and verify attachment readback. On resume, inspect already attached files before
asking for another upload. Follow [checkpoint and
recovery](docs/workflows/checkpoint-recovery.md#контрольная-точка-и-восстановление). Do not equate a saved commit, a
passed check, a delivery receipt, and Task completion.

## Stage skills and missing expertise

The [two-set stage-skill contract](docs/workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
is a draft for later tooling: copy meta and subject skill defaults from the selected
stage template into a new Task, then let its planner remove/add skills for each stage.
Executor and reviewer use the assigned current-stage skills; suggestions must not
silently replace the planner's selection. Do not invent unsupported Task/template fields
or claim automatic enforcement. If a needed specialist skill is missing or insufficient,
report the stage, missing expertise, impact and proposed planner action in the final
answer; raise actual blockers immediately. Follow [skill-gap
reporting](docs/workflows/task-stage-skills-draft.md#использование-навыков-и-сообщение-о-нехватке).

## Commit message example

Describe the final product effect and why it matters, not which files/classes changed.
Use a subject of at most 50 characters, one blank line, and meaningful body lines of
at most 70 characters. For an authorized direct commit of reviewed staged files:

```bash
git commit -m 'Commit messages explain user-visible outcomes' \
  -m 'Readers can understand the effect and purpose of each change.'
```

This is Git, not an installed validator or permission to bypass Task lifecycle.
Use the same message format through Poise's existing `commit_message` when its
workflow owns the commit. See [message policy](docs/workflows/commit-messages.md#продуктовый-смысл-и-формат-5070)
and [execution limits](docs/workflows/commit-messages.md#запуск-и-границы-полномочий).

## Shared agent policy

Keep at most one Task and one worktree per session, with independent claims and all
four combinations. Acquire the complete set atomically through its owner; never steal
an uncertain live claim. Automatic acquisition is distinct from explicit user-authorized
[after-crash revocation](docs/workflows/crash-ownership-recovery.md): restore into isolated paths,
confirm stopped writers, use the existing recover_ownership template and preserve
Task history, WIP and pending outcomes. Never fabricate SessionEnd or reuse a revoked caller. The process snapshot determines a dependent worktree, which
is released with its Task; preserve an independent worktree and all WIP, cwd and launch
roots. Claim replacement is not completion, cleanup or integration authority. Follow
the canonical [ownership rule](docs/governance/development-rules.md#владение-task-и-worktree).
At a role boundary the sender saves results, confirms public release, then directly messages the known counterpart;
the recipient acquires before mutation. Existing user Task authorization covers ordinary review and remediation.

The canonical policy is [Development
rules](docs/governance/development-rules.md#%D0%BE%D0%B1%D1%89%D0%B8%D0%B5-%D0%BF%D1%80%D0%B0%D0%B2%D0%B8%D0%BB%D0%B0-%D0%B0%D0%B3%D0%B5%D0%BD%D1%82%D0%BE%D0%B2).
Keep this English projection and its Russian source consistent in the same change.

- Answer humans and write human-facing documentation in Russian. Write agent-facing files (`AGENTS.md`, `.agents/**`,
  `.codex/**`, managed task artifacts) in English. Preserve native identifiers and syntax.
- Canonical human documentation owns durable workflow semantics. Skills, agent rules, configurations and scripts
  implement that contract; historical plans and reports are not independent policy. Co-deliver affected documentation
  with behaviour, configuration or workflow changes.
- Keep the existing path resolution of AI poise-owned hooks, tools, runtime code and configuration. Pass a checkout or
  working directory as an explicit filesystem path/cwd only to the operation that needs it; see [path resolution and
  working copies](docs/migrations/erp-runtime-migration-retrospective-2026-09-15.md#разрешение-путей-и-рабочие-копии).
- Keep Requirements at exactly three semantic levels: System → Application → Task. Bottom-up Task discovery may create proposed System/Application requirements. Future/projected chains may use explicit typed empty placeholders at missing levels once the owner supports them; placeholders are gaps, not satisfaction and not a fourth level. Virtual grouping is orthogonal. Never invent ancestry when the mapping is unclear; ask for the missing decision.
- Reuse evidence and artifacts through their owners. Repository evidence is valid only for the exact commit hash it proves (plus its compatible requirement/method provenance); a changed commit hash requires fresh evidence. Do not manually copy receipts to manufacture reuse.
- Read applicable nested agent rules before working on their surface. Use specific rules within root constraints,
  subject to higher-priority platform and user instructions. Load only skills and reference sections needed for the
  active phase.
- Continue authorized work to its stated outcome, without expanding scope. Do not delegate to sub-agents without
  explicit user authorization in the current request. Ask only for a genuinely blocking decision; use existing
  authorization for routine work.
- For a user-started Task, known executor/reviewer counterparts continue autonomously within AI poise gates and user
  limits. In both directions, save results, confirm public Task release, then send the counterpart a direct handoff
  message; the recipient acquires before working. No per-review user command is required. Follow [direct role
  handoff](docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим); messaging never replaces
  ownership or independent review.
- Use the public `advance` operation with one stable request identity when work must reach an explicit later stage.
  Replay it after each verified current-role stage, lawful correction or restart. It stops for real work, entry gates,
  role boundaries and separately controlled publish acceptance; after public handoff, the receiving session acquires the
  Task and replays the exact target. It never performs stage work, messaging, user acceptance, publication or
  integration.
- Keep received assignments as outstanding obligations until completed, cancelled or explicitly transferred. New
  messages add to the working plan unless they explicitly change an assignment; preserve pending work and blockers
  across checkpoints and compaction. Reading or acknowledging a message does not complete its assignment.
- After saving results, confirming Task release and notifying the counterpart, immediately continue the next
  already-received actionable assignment or authorized stage/Task chain in the same turn. An idle reviewer starts on a
  received handoff; otherwise finish the review already started before taking the next Task. Incoming handoffs do not
  interrupt that review or claim a second Task. End the turn only when no authorized actionable work remains; first
  account for every pending assignment and its blocker. Finish your own already-started operations. Do not poll
  counterpart status, read its conversation for progress or schedule background monitoring; checking your own pending
  assignments is required. See [direct role
  handoff](docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим).
- Inspect Git state before the first write. Preserve unrelated changes. If a modified or untracked path's ownership is
  unclear, obtain an explicit user decision before altering it. Never reset, restore, stage or commit unrelated files.
- Do not create a new top-level repository directory without explicit user authorization naming its path and purpose.
  Keep temporary runtime files and managed task/sprint evidence in the configured AI poise locations, under the
  explicitly configured state root. For this ai-poise installation, the user authorized `projects/ai-poise/` inside the
  repository; it is ignored by Git.
- File extensions must match content and purpose. Environment templates end in `.env` and contain `example`, such as
  `app.example.env`. Format parallel lists vertically for stable diffs. Invoke repository `.sh` entry points explicitly
  through Bash.
- Product-affecting values come from explicit configuration contracts. Internal constants are allowed only when they
  cannot affect product behaviour or output. Do not add hidden defaults, fallbacks, legacy aliases, dual reads/writes or
  compatibility adapters unless explicitly required. Accepted replacements remove the superseded path within scope;
  missing required inputs fail explicitly.
- Read skill classes and responsibility boundaries from project-specific routing. Every process phase declares its
  skills and areas before Task readiness. Ordinary Tasks cannot combine peer narrow responsibilities; integration Tasks
  require explicit integration fields. Across all phases, every area routed to a policy-owned narrow responsibility must
  match a declared narrow-skill responsibility; other routes do not become narrow because of phase placement. Phase
  splitting does not bypass the boundary, and declaration order does not change the decision or diagnostic.
  Decomposition validation does not prove factual completeness.
- Report an unattainable DoD or a failed next-stage DoR as `broken`, not completion. Recover by repairing only the
  defective stage contract or by using the public Task `restart` action to return the same unfinished, unintegrated Task
  to newborn while preserving its identity, history, Sprint membership, worktree and WIP. Resolve pending external
  uncertainty first. Do not use or restore Sprint `replace_task` as a correction path; preserve historical relations as
  opaque revision/audit provenance, not a typed runtime projection.
- Measure accounting duration and ordering only from comparable monotonic observations supplied through the required
  `Clock` boundary. Use UTC only for audit and calendar projection. Production composition roots explicitly provide
  `SystemClock`; tests provide a deterministic `FakeClock`. Never clamp wall-clock regressions, invent elapsed time
  across comparison domains, or add a hidden clock fallback.
- Search for existing behaviour and its domain owner before implementation. Reuse the owning API or extract one shared
  implementation; keep consumers focused on composition and transport. Separate implementations require distinct
  semantics or ownership.
- For code-semantic work, distinguish JetBrains MCP `configured`, model-visible `listed`, worktree `project_bound`, and
  separately `operation_proved` states. When a listed IDE capability applies to the current worktree, use it for
  semantic navigation, call relationships, inspections, semantic rename, or IDE-owned formatting within the stage scope;
  do not route build/run, debugger, generic file/terminal, VCS, lifecycle, managed configuration, or database work
  through IDE MCP. Record the reason before an unavailable or inapplicable IDE operation falls back to ast-index; use
  ast-index directly for repository-wide graph, batch, and structural search. Follow the exact [selection
  rule](docs/governance/jetbrains-mcp-policy.md#правило-выбора-инструмента),
  [boundaries](docs/governance/jetbrains-mcp-policy.md#границы-и-исключения),
  [fallback](docs/governance/jetbrains-mcp-policy.md#fallback-на-ast-index), and [evidence
  contract](docs/governance/jetbrains-mcp-policy.md#проверка-и-evidence). For AI-poise development, apply the [existing
  F1 tool owners](docs/governance/jetbrains-mcp-policy.md#применение-поставленных-инструментов-f1); a concrete inspected
  folder does not change how the running Poise resolves tools or hooks.
- Inter-task dependencies exist only inside one Sprint. Keep a Task with no incoming or outgoing inter-task edge
  standalone. Every Sprint must contain one connected dependency chain covering its complete local prerequisite graph,
  not independent tasks or disconnected chains; never fabricate edges. When splitting a long Sprint, duplicate required
  Tasks or prerequisite chains locally with new IDs and explicit instructions to check whether their requirements are
  already implemented before doing any implementation. Do not search semantically similar Tasks or manually maintain
  reciprocal duplicate lists. The implemented duplicate behavior has two independent axes: duplicate versus ordinary
  Task, then parent versus child for every duplicate. A root with children is itself a parent duplicate; both roles call
  one shared library method: resolve the original parent, list all its children, then batch-read the parent and all
  children's current stages, processes, statuses and session owners in a consistent DB read. Only parent resolution
  depends on role; context and stage admission reuse this method, not per-task queries or duplicated traversal. Compute
  d as the shortest directed transition count from the current stage to positive termination (equivalently traverse
  reversed edges from positive terminals). Use current position after rework, not stage index, stage-name equality or
  maximum past progress. Success has d=0; cancellation is not success. Include the current Task in d_min over its
  noncancelled family. For ordinary repeated implementation, block stage start whenever d(current)>d_min, even by one; equality is necessary but not
  sufficient: reject by default if two or more nearest Tasks have sessions, counting the requesting session
  prospectively before claiming an unowned current Task. After agents agree one executor, only an explicit per-request
  force_duplicate_start=true may clear this tied-session refusal. The flag is an optional boolean on bootstrap and
  advance; it never overrides lagging distance, invalid data, foreign ownership or stage prerequisites. Return only
  every other family member at d_min with Task/Sprint IDs, stage, distance, status, iteration and owning session ID.
  Never combine leaders with tied-but-lagging peers or return intermediate distances. Equal minimum distances may occur
  at different stages. Multiple sessions at that minimum require agreement and the explicit flag even at initial stages.
  Unowned equal Tasks alone do not create a collision. Reread family state on every request, including a flagged retry;
  never persist or automatically add the flag. Check at acquisition, resume and every actual stage start; refreshed
  context is not a substitute for the gate. This specifies enforced refusals for lagging distance and unconfirmed
  tied-session starts; it does not stop or seize foreign work. Missing or invalid distances must not create permission.
  Reuse still requires main-base integration and local availability; it must not bypass a denied normal stage start,
  fabricate terminal progress or inherit another Task's acceptance. Use the implemented work/reuse operation with
  explicit task_id, source_task_id, request_id and the original expected_version to verify an accepted, integrated
  family result locally. It returns reuse_verified, not completed; a separately authorized accept rechecks provenance
  and local receipts before completing the Task. Exact replay preserves the original packet; terminal replay returns
  accepted history without reacquiring or recreating a cleaned worktree. Native handoff validates the preserved bundle
  and consumes its receipt on reuse resume. Reuse now automatically delivers accepted permanent Task/Sprint artifacts
  through the existing publisher with local owner identities, no overwrites, digest checks and local registry links.
  Missing required files or conflicts reject reuse; optional empty sets are allowed by their actual contracts. Do not
  copy foreign runtime-session files or receipts. Use work/task action duplicate with an explicit parent_id and destination draft sprint_id;
  no manual family searches or SQLite edits. See [dependency closure and local
  duplicates](docs/workflows/sprints.md#замкнутость-зависимостей-и-локальные-дубли). Use `adopt_tasks` atomically only
  for Tasks with `status=available, claimed_by=null, worktree=null, pending=null, last_report=null, and attempts=0`. Use
  `extract_tasks` only for the same eligible published member with no incoming or outgoing dependency. Identity,
  immutable history, goal, contract, and readiness are preserved. Success changes membership, graph, revision, and the
  request receipt together; rejection changes none of them, and a retry after rejection is not a replay. Draft
  `remove_tasks` remains the pre-publication correction route, and cancelling a draft atomically detaches both newborn
  and adopted Tasks.
- Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks
  and the maintained bounded `tests/smoke.sh`; never register unfiltered repository-wide test discovery as a task
  method. After the final relevant change, inspect the complete change. Documentation-only work requires content, link
  and executable-claim validation, not an invented RED phase. Do not present self-review as independent review or
  stale/missing/failed evidence as a pass.
- Change the current verification registry with one guarded batch only at a stage that owns `test_registry`. The sole
  narrow exception is replacement of a current `observe` stage's own subject method under the immutable scope and audit
  contract in [stale observation replacement](docs/workflows/evidence.md#замена-устаревшего-метода-наблюдения). Creation
  for a process schema that owns `test_registry`, and every registry mutation, explicitly provide
  `executable_obligations`; never infer the classification. A process without that schema field must omit it, and Task
  creation initializes the public registry projection with an explicit empty list. Preserve historical definitions and
  receipts. Test-inspection exit requires a non-empty current GREEN executable set whose collective coverage includes
  every declared executable obligation, but does not require obsolete methods or an unfiltered repository-wide suite.
- Pass the complete initial checks object explicitly when creating a development Task; `{}` is a valid complete
  schedule. Design and register later RED/GREEN methods, schedules and future-output provenance at
  `verification_planning`, after their real sources are known.
- Verification methods do not declare execution timeouts. Once a verification or declared action command starts, wait
  for its terminal result; do not convert elapsed time into an unknown execution outcome.
- When execution is stale at `pending=checks`, replay only the exact submitted `verify` packet. Recovery is allowed
  solely for a complete, provenance-matching set of terminal receipts and must not rerun checks; incomplete, mismatched,
  duplicated, unknown, or output-corrupt receipts remain protectively rejected without mutation.
- Use supported repository tools and configured checks. Diagnose failures and timeouts from evidence; do not bypass a
  failing check or increase a timeout to conceal a hang. Preserve failed diagnostics. Do not invent unavailable usage
  measurements or tool capabilities.
- User-facing errors explain the failure, verified cause or remaining uncertainty, and an actionable recovery within the
  user's authority. Do not expose secrets or invent certainty.
- Destructive migrations require explicit authorization naming the objects and action. Never execute `git push`: the
  prohibition is absolute for every agent, AI poise tool, publication handler, and configurable command runner, and user
  publication authority does not waive it. Reject `push_required=true` before any Git command; `push_required=false`
  never contacts a remote. Integrate accepted results only through the public local `integrate` lifecycle using `git
  merge --ff-only`. Commits required by the documented workflow of an explicitly assigned task are authorized without
  another confirmation, including the necessary baseline and result commits. Include only the task-owned or explicitly
  approved files; unrelated commits still require explicit authorization. Follow AI poise's public lifecycle tools when
  available.
- Finish accepted work only through the public result-integration lifecycle running from the current AI poise
  installation source. Keep the accepted commit immutable; advance the existing task branch in its existing task
  worktree while updating from current `master`, merging, resolving conflicts, and rerunning every current
  produced-result GREEN registry method whose `green_stages` and `change_surface` are nonempty. The terminal
  content-stage schedule does not narrow integration checks; exclude RED and baseline-only guards. Never create a
  separate integration branch or worktree. Under the shared target lock, recheck `master` immediately before
  publication; repeat update, resolution, and checks on drift, then publish only with `git merge --ff-only
  <task-branch>` in the main checkout. Never update the target ref directly or force-update it. If fast-forward is
  blocked, prove that main `HEAD`, binding, index, tracked/untracked content, types, modes, and operation state are
  unchanged.
- At every terminal task outcome, the responsible agent completes handling by cleaning the exact task-owned worktree,
  local branch, temporary files, and temporary backups. Preserve the main checkout, foreign resources, durable task
  history and artifacts, operator backups, and recovery data for unfinished operations.
- The commit disposition requires a separate explicit decision. Cleanup itself authorizes neither publication to
  `master` nor destruction of unique commits and does not prescribe merge or discard. Clean accepted successful work
  only after its separately authorized integration completes. On cancellation, record a separate commit-disposition
  decision before removing a resource would destroy unique commits.
- These rules establish a manual agent obligation. This documentation task neither implements cleanup automation nor
  claims that it does; the pre-existing public result-integration capability is governed separately by the canonical
  Russian documentation.
- Never use the main checkout or foreign WIP for preparation or conflict resolution, and never `stash`, `reset`,
  `restore`, `checkout`, `clean`, stage, commit, or delete their state. The sole publication effect is the serialized
  `git merge --ff-only`; a dirty or unfinished checkout may block it without being modified. After confirmed
  publication, the responsible agent cleans only the task worktree, task branch, and registered temporary backups.
  Temporary backups belong only in a task/integration-scoped directory under the configured runtime root, and success
  requires that directory to be empty. Never delete pre-existing, foreign, durable operator, deliverable, or
  unfinished-recovery backups. Persist enough phase state for crash recovery and idempotent replay without rewriting the
  accepted commit.
- Report the result, requirement coverage, checks actually performed, failures or unavailable checks, and remaining
  uncertainty. A verified stage is not user acceptance; it authorizes continuation only when the existing assignment
  covers the next executor-owned stage.
- In independent review, follow the canonical [evidence-based checklist](docs/workflows/evidence-based-review.md): tie
  every finding to a confirmed obligation, producer stage, later gate and reproducible check; classify AI poise
  runtime/task-contract, executor, reviewer, protective-rejection and unresolved causes from evidence. Batch independent
  managed reads through existing `show.queries`; do not add another reader or claim unmeasured savings.

## Mandatory bootstrap and verification

Follow [Start and
finish](docs/governance/development-rules.md#%D0%BD%D0%B0%D1%87%D0%B0%D0%BB%D0%BE-%D0%B8-%D0%B7%D0%B0%D0%B2%D0%B5%D1%80%D1%88%D0%B5%D0%BD%D0%B8%D0%B5-%D1%80%D0%B0%D0%B1%D0%BE%D1%82%D1%8B)
and the [batch API](docs/workflows/batch-work.md#verify).

Before substantive project work, explicitly select the working project configuration and session identity, then invoke
`bootstrap` through AI poise's public work API. Only prerequisite inspection needed to locate/configure that entry point
precedes it. Consume the returned task/stage, capabilities and result template; do not invent task IDs, state or a
worktree. Resume through the same API.

Before reporting successful completion, invoke `verify` through the same API with the current stage result and
artifacts, inspect its terminal status and required evidence, and complete any required continuation within the
authorized stage. A zero exit code, pending continuation or mere receipt is not proof of success. Continue to the next
executor-owned stage when the existing executor assignment covers it; do not enter reviewer work or separately
controlled acceptance/publication/integration without the applicable authorization.

Repository snapshot Git-index ownership and recovery follow the canonical [batch-work
contract](docs/workflows/batch-work.md#изоляция-временного-git-index). Never delete `snapshot.index.lock` manually or
remove another invocation's snapshot directory.

A current exact terminal failed-check batch authorizes rework to any configured target, including a `revise` stage with
no open finding; that no-finding revise submits no reviewer resolutions. Neither this path nor ordinary rework may
bypass pending resolution inspection: continue to the exact inspection stage named by Poise, inspect every pending
resolution, and only then request rework. Use documented legacy recovery solely for already-stranded tasks. See
[explicit failed-check rework](docs/workflows/batch-work.md#явный-rework-после-checks_failed) and [rework with pending
resolutions](docs/workflows/batch-work.md#rework-при-нерассмотренных-исправлениях) (Tasks 0124 and 0063 RD-013).
A persisted active inspection blocked by its current registry exit gate may use explicit user rework to a configured
target that owns `test_registry`. Task revalidates the gate inside the command UoW; preserve candidate findings,
history, immutable evidence and WIP. Do not manufacture a failed check batch or bypass pending external outcomes or
unresolved reviewer resolutions. This narrow path does not authorize rework for unrelated active states. See
[blocked-registry inspection recovery](docs/workflows/batch-work.md#rework-при-заблокированном-реестре-inspection).


For taskless read-only work, use `bootstrap` with explicit null task/decision/feedback/rework_stage and `verify` with
`result=null`, `artifacts=[]`. Confirm that the returned context is actually taskless; a session may already own work.
An addressed `completed` or `cancelled` Task returns a `terminal inspection snapshot` containing its preserved context,
content, evidence, and history; inspection must not bind, resume, or claim that Task. Use that snapshot directly, then
use taskless bootstrap before null-result verify. Cancellation is an emergency terminal outcome and does not require
evidence. `read_only_verified` finalizes runtime with no substantive checks and must not be represented as validation of
the answer. Independently validate factual claims. Persistent task results require a formal task and the managed
artifact API.

If configuration, runtime or a required operation is unavailable, report the exact
blocker and observed state; do not substitute sample settings, a reference database,
a fabricated identity or successful harness status. When Poise blocks otherwise
executable subject work already authorized by the user, continue only that subject
work manually, preserving real changes, checks, WIP and the incident. Do not repair
Poise or create a parallel task harness without a separate assignment. Distinguish
subject-work results from unperformed Poise operations; a broken ceremony is not a
reason to fabricate evidence or abandon separately authorized work. Follow
[configured-runtime preflight and operator
restoration](docs/configuration/runtime-hooks.md#предварительная-проверка-настроенной-среды).
Repository instructions do not by themselves install an automatic enforcement hook.

## Executable entry point for this installation

`bootstrap` and `verify` are JSON operations of AI poise's existing `work` CLI, not standalone skills, shell commands or
MCP tools. This installation uses only the `codex-hook-main` message source with `mode=runtime_event`. Invoke the
session-scoped `work.sh` supplied by the native hook explicitly through Bash, with the work packet on stdin and
`messages=[]`. The launcher selects `/home/sbwerewolf/workdata/ai-poise/config/projects/ai-poise/project.json` and the
native session binding; use the same launcher across calls. Never report or replay user messages manually, generate a
substitute session, or silently switch to another message source.

If the native hook has not supplied a launcher, complete hook discovery/trust and diagnose its execution before task
work. Direct `/home/sbwerewolf/workdata/ai-poise/.venv/bin/poise work` with explicit `POISE_CONFIG` is available for
installation diagnostics; when no native Codex identity exists, also provide an absolute persistent
`POISE_CALLER_BINDING` whose parent already exists. The agent must not substitute its own session value for the native
or persisted caller identity. Diagnostic packets keep `messages=[]`; direct identity establishment does not prove native
event delivery. The installation contract and recovery steps are in the local project documentation below.

Read [Local ai-poise
commands](docs/configuration/project-setup.md#%D0%BB%D0%BE%D0%BA%D0%B0%D0%BB%D1%8C%D0%BD%D1%8B%D0%B9-%D0%BF%D1%80%D0%BE%D0%B5%D0%BA%D1%82-ai-poise)
for complete runnable bootstrap/verify packets. Existing workflow instructions are discovered from the [AI poise
workflow](.agents/skills/poise/SKILL.md) project skill. Do not search for nonexistent skills named `bootstrap` or
`verify`.

This installation stores mutable data under `/home/sbwerewolf/workdata/ai-poise/projects/ai-poise/`: `.runtime/`,
`standalone/`, `sprint/`, and `database/`. Standalone Task artifacts belong in `standalone/<task-id>/`; Sprint-member
Task artifacts belong in `sprint/<sprint-id>/task/<task-id>/`. The live Task DB is `database/tasks.sqlite`, its lock is
`database/tasks.lock`, and backups belong only in `database/backups/`. Do not create compatibility files or symlinks for
the old root-level database paths or the removed `task/` artifact root. This is the user's explicit local placement
decision, superseding the external-state default for this project. Configuration is under `config/projects/ai-poise/`,
separate from mutable data.

Discover and operate Task DB backups through the public `poise backup` CLI. Start with `poise backup help`; use `poise
backup list --config PROJECT_JSON`, `poise backup create --config PROJECT_JSON`, and `poise backup restore --config
PROJECT_JSON BACKUP_NAME` rather than copying or replacing the database manually. Run create and restore only while no
agent or process is writing the Task DB. The complete operator contract is [Task DB backups](docs/task-db-backups.md).

The initial manifest has `push_required=false`, base ref `master`, and repository readiness `not_checked` because no
initial commit existed at setup. Taskless bootstrap/verify do not establish Git/worktree readiness. Before
repository-changing task work, verify that the configured base resolves to a commit and contains the required sources. A
baseline commit needed to execute the assigned task is authorized by the task workflow; include only the approved
project files and preserve unrelated changes.
Every new or explicitly changed verification method must declare an exact
`verification_plan` with `responsibility`, `change_surface`, `red_stages`,
`green_stages`, and `red_failure`. Its schedule must match the plan, and every
route to a required GREEN must pass through a stage whose `allowed_paths` covers
each declared surface item. A repository method may declare an empty surface only as a
pre-existing baseline guard: no RED, sole GREEN at the route-entry `baseline`; produced-result
GREEN methods require a non-empty covered surface. RED uses exact stdout/stderr equality; additional
failures invalidate it. Do not infer recursive test dependencies. Stored
pre-plan snapshots remain readable, but this is not a fallback for new methods.

For bounded source/document reads, use [explicit ranges and context
generations](docs/workflows/source-reader.md#явные-диапазоны-и-поколения-контекста). Do not suppress reads on
unacknowledged delivery or carry read-memory assumptions across compaction/resume.

After compaction/resume, use [current-owner context
recovery](docs/workflows/context-recovery.md#возобновление-после-compaction-и-resume). Restore Task/route facts through
their owners; reread only explicit ranges and never infer remembered text from old receipts.

Keep optional telemetry persistence and retries off the work result path. Do not equate
in-memory acceptance with durable delivery. Follow [bounded telemetry
delivery](docs/operations/telemetry-delivery.md#асинхронная-доставка-без-блокирования-работы);
replay is an explicit operation or a bounded worker step, never a Task gate.

Keep source API metric parameters and their source timestamps unchanged; do not infer
missing durations or equate delivery time with observation time. Follow
[source metric
provenance](docs/operations/source-api-metrics.md#сохранение-фактов-источника-без-вычисления-длительностей).

For explicit hook/runtime effect diagnosis use
[observable hook effects](docs/operations/hook-effect-diagnostics.md#диагностика-фактических-эффектов-hooks).
Installation, native event observation, owner effect, host trust and Task completion are different claims.
Do not start a standing observer or infer successful effects from hook registration alone.

## Cloud-agent recovery

Use [the cloud-agent recovery toolkit](recovery-tools/README.md#назначение-и-границы)
for complete project checkpoints, binary/readable Gmail transport and isolated
cloud/local reconciliation. It reuses the existing checkpoint owner and never
replaces live inputs. Treat Task ID collisions as identity decisions, not equality.
Follow [guarded SQL plans](recovery-tools/README.md#транзакционный-sql-план) for
divergent database copies; preserve both histories and check the complete graph.
A user-authorized workaround for a broken workflow never permits fabricated
checks, loss of WIP or violated database integrity. Update an existing incident
Task only with materially new information. Keep root HANDOFF.md and generated
RECOVERY-MANIFEST.json untracked; include them in the delivery, not commits.


Native caller isolation: a session launcher is not caller identity. Use only the launcher
for the host-observed CODEX_THREAD_ID/CODEX_SESSION_ID; both must agree when present.
Never set these values to impersonate a stored binding or combine native identity with
POISE_CALLER_BINDING. Configured agent_id is a profile, not a separate actor. A child with
parent-only signals must not mutate the parent's Task. The sender uses public handoff;
a genuinely separate native session obtains its own SessionStart launcher and bootstraps
the exact Task. Preserve WIP and diagnose missing/conflicting signals instead of renaming
roles, overriding native identity or editing Task DB to get past the guard.

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
See [the recovery contract](docs/workflows/sprints.md#локальная-доработка-через-общий-restart).
