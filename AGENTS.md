# Working on AI poise

A direct user instruction may temporarily override a project process rule. Request an exception only when correct completion is otherwise impossible; explain the rule, the blocked problem, the risks, the scope and when the exception expires. Platform safety constraints and storage integrity are not project rules that this mechanism can disable.

## Purpose and outcomes

Reduce the model tokens spent administering tasks. Keep bookkeeping, repeated mechanical actions and unnecessary tool calls out of the agent's work so that its effort goes into sound engineering decisions, useful code and clear documentation.

Before any work, including a request without a formal task, establish the goal, the requirements for the result, the definition of done and how completion will be demonstrated. Keep this preparation proportional to the request.

Use test-driven development for executable behaviour changes: design the checks and write the tests before implementing the behaviour. Review the tests, implement the change, run the checks, and inspect the code and subsequent fixes. Do not present self-review as independent review.

Turn every accepted agreement into product documentation. Make DDD decisions enforceable development rules and keep responsibility boundaries documented. Do not leave authoritative decisions only in conversation history.

Deliver the working path rather than delaying it for speculative combinations of conditions. Handle the ordinary routes and failures implied by the rules; resolve uncertain edge cases using actual usage and measurements. State current limits honestly.

## Start and finish work

Work from this AI poise repository and explicitly select the configured project. Target codebases have their own applicable `AGENTS.md` files; read those before working on them. Use the [AI poise skill](.agents/skills/poise/SKILL.md) for task work and the [development skill](.agents/skills/poise-development/SKILL.md) when changing AI poise itself. The [source rules](src/AGENTS.md) govern its implementation.

Before changing code, use a dedicated Git worktree. For AI poise, use `tasks/<task-id>`; target applications use their configured branch rule. Do not require a worktree for a read-only summary. Git supplies changed files; the agent does not register them manually.

One session works on one task. Complete the requested stage, verify it, report the result and stop until the next user instruction. Before changing tasks, finish, hand off or safely discard the current work. A read-only query about another task does not switch ownership.

Use `poise project` or `poise project-init` with an explicitly selected template to prepare a new project; do not hand-edit its managed configuration. See [project setup](docs/configuration/project-setup.md).

Use the existing declarative batch tools for managed configuration, task/sprint data and artifacts; do not edit their working files or database directly. Native coding tools remain appropriate for source code, tests and target documentation. Use the current user-authorized scope and applicable canonical documentation; historical plans do not grant ongoing authorization. Report implemented and tested capabilities separately from planned ones.

Count observed user messages without inventing missing messages or token usage. Preserve their source and coverage. Report AI poise incidents even when recovery succeeded; ordinary test failures are work results, not automatically AI poise defects.

## Shared agent policy

The canonical policy is [Development rules](docs/governance/development-rules.md#%D0%BE%D0%B1%D1%89%D0%B8%D0%B5-%D0%BF%D1%80%D0%B0%D0%B2%D0%B8%D0%BB%D0%B0-%D0%B0%D0%B3%D0%B5%D0%BD%D1%82%D0%BE%D0%B2). Keep this English projection and its Russian source consistent in the same change.

- Answer humans and write human-facing documentation in Russian. Write agent-facing files (`AGENTS.md`, `.agents/**`, `.codex/**`, managed task artifacts) in English. Preserve native identifiers and syntax.
- Canonical human documentation owns durable workflow semantics. Skills, agent rules, configurations and scripts implement that contract; historical plans and reports are not independent policy. Co-deliver affected documentation with behaviour, configuration or workflow changes.
- Read applicable nested agent rules before working on their surface. Use specific rules within root constraints, subject to higher-priority platform and user instructions. Load only skills and reference sections needed for the active phase.
- Continue authorized work to its stated outcome, without expanding scope. Do not delegate to sub-agents without explicit user authorization in the current request. Ask only for a genuinely blocking decision; use existing authorization for routine work.
- Inspect Git state before the first write. Preserve unrelated changes. If a modified or untracked path's ownership is unclear, obtain an explicit user decision before altering it. Never reset, restore, stage or commit unrelated files.
- Do not create a new top-level repository directory without explicit user authorization naming its path and purpose. Keep temporary runtime files and managed task/sprint evidence in the configured AI poise locations, under the explicitly configured state root. For this ai-poise installation, the user authorized `projects/ai-poise/` inside the repository; it is ignored by Git.
- File extensions must match content and purpose. Environment templates end in `.env` and contain `example`, such as `app.example.env`. Format parallel lists vertically for stable diffs. Invoke repository `.sh` entry points explicitly through Bash.
- Product-affecting values come from explicit configuration contracts. Internal constants are allowed only when they cannot affect product behaviour or output. Do not add hidden defaults, fallbacks, legacy aliases, dual reads/writes or compatibility adapters unless explicitly required. Accepted replacements remove the superseded path within scope; missing required inputs fail explicitly.
- Measure accounting duration and ordering only from comparable monotonic observations supplied through the required `Clock` boundary. Use UTC only for audit and calendar projection. Production composition roots explicitly provide `SystemClock`; tests provide a deterministic `FakeClock`. Never clamp wall-clock regressions, invent elapsed time across comparison domains, or add a hidden clock fallback.
- Search for existing behaviour and its domain owner before implementation. Reuse the owning API or extract one shared implementation; keep consumers focused on composition and transport. Separate implementations require distinct semantics or ownership.
- Run focused checks after the final relevant change and inspect the complete change. Documentation-only work requires content, link and executable-claim validation, not an invented RED phase. Do not present self-review as independent review or stale/missing/failed evidence as a pass.
- Verification methods do not declare execution timeouts. Once a verification or declared action command starts, wait for its terminal result; do not convert elapsed time into an unknown execution outcome.
- Use supported repository tools and configured checks. Diagnose failures and timeouts from evidence; do not bypass a failing check or increase a timeout to conceal a hang. Preserve failed diagnostics. Do not invent unavailable usage measurements or tool capabilities.
- User-facing errors explain the failure, verified cause or remaining uncertainty, and an actionable recovery within the user's authority. Do not expose secrets or invent certainty.
- Destructive migrations require explicit authorization naming the objects and action. Never push. Commits required by the documented workflow of an explicitly assigned task are authorized without another confirmation, including the necessary baseline and result commits. Include only the task-owned or explicitly approved files; unrelated commits still require explicit authorization. Follow AI poise's public lifecycle tools when available.
- Finish accepted work only through the public result-integration lifecycle. Keep the accepted commit immutable and perform every update from current `master`, merge, and conflict resolution in a child task/integration worktree with a separate mutable integration head. On conflict, stop for agent resolution there and rerun checks afterward. Recheck `master` immediately before publication; if it moved, automatically repeat update, resolution, and checks until stable, then safely fast-forward `master` without manual supervision or a force update.
- The main checkout and foreign WIP are neither prerequisites nor integration surfaces. Never use `stash`, `reset`, `restore`, `checkout`, `clean`, staging, commit, deletion, or conflict resolution on them for task completion. After confirmed publication, the same finisher removes the task/integration worktrees, child branches, and registered temporary backups. Temporary backups belong only in a task/integration-scoped directory under the configured runtime root, and success requires that directory to be empty. Never delete pre-existing, foreign, or durable operator backups; retain a deliverable or unfinished-recovery backup until a separate terminal decision. Persist enough phase state for crash recovery and idempotent replay.
- Report the result, requirement coverage, checks actually performed, failures or unavailable checks, and remaining uncertainty. A verified stage is not user acceptance and does not authorize the next stage.

## Mandatory bootstrap and verification

Follow [Start and finish](docs/governance/development-rules.md#%D0%BD%D0%B0%D1%87%D0%B0%D0%BB%D0%BE-%D0%B8-%D0%B7%D0%B0%D0%B2%D0%B5%D1%80%D1%88%D0%B5%D0%BD%D0%B8%D0%B5-%D1%80%D0%B0%D0%B1%D0%BE%D1%82%D1%8B) and the [batch API](docs/workflows/batch-work.md#verify).

Before substantive project work, explicitly select the working project configuration and session identity, then invoke `bootstrap` through AI poise's public work API. Only prerequisite inspection needed to locate/configure that entry point precedes it. Consume the returned task/stage, capabilities and result template; do not invent task IDs, state or a worktree. Resume through the same API.

Before reporting successful completion, invoke `verify` through the same API with the current stage result and artifacts, inspect its terminal status and required evidence, and complete any required continuation within the authorized stage. A zero exit code, pending continuation or mere receipt is not proof of success. Do not accept a task or start the next stage without the applicable authorization.

For taskless read-only work, use `bootstrap` with explicit null task/decision/feedback/rework_stage and `verify` with `result=null`, `artifacts=[]`. Confirm that the returned context is actually taskless; a session may already own work. `read_only_verified` finalizes runtime with no substantive checks and must not be represented as validation of the answer. Independently validate factual claims. Persistent task results require a formal task and the managed artifact API.

If configuration, runtime or a required operation is unavailable, report the concrete blocker and recovery needed; do not substitute a sample configuration, reference database or invented successful status. Successful completion remains unverified until the required operation succeeds. Repository instructions describe the obligation; they do not by themselves install an automatic enforcement hook.

## Executable entry point for this installation

`bootstrap` and `verify` are JSON operations of AI poise's existing `work` CLI, not standalone skills, shell commands or MCP tools. This installation uses only the `codex-hook-main` message source with `mode=runtime_event`. Invoke the session-scoped `work.sh` supplied by the native hook explicitly through Bash, with the work packet on stdin and `messages=[]`. The launcher selects `/home/sbwerewolf/workdata/ai-poise/config/projects/ai-poise/project.json` and the native session binding; use the same launcher across calls. Never report or replay user messages manually, generate a substitute session, or silently switch to another message source.

If the native hook has not supplied a launcher, complete hook discovery/trust and diagnose its execution before task work. Direct `/home/sbwerewolf/workdata/ai-poise/.venv/bin/poise work` with explicit `POISE_CONFIG` and a preserved operator `POISE_SESSION` is available for installation diagnostics only; it does not establish a native binding or prove event delivery. Keep `messages=[]` in those diagnostic packets too. The installation contract and recovery steps are in the local project documentation below.

Read [Local ai-poise commands](docs/configuration/project-setup.md#%D0%BB%D0%BE%D0%BA%D0%B0%D0%BB%D1%8C%D0%BD%D0%B8%D0%B9-%D0%BF%D1%80%D0%BE%D0%B5%D0%BA%D1%82-ai-poise) for complete runnable bootstrap/verify packets. Existing workflow instructions are discovered from the [AI poise workflow](.agents/skills/poise/SKILL.md) project skill. Do not search for nonexistent skills named `bootstrap` or `verify`.

This installation stores mutable data under `/home/sbwerewolf/workdata/ai-poise/projects/ai-poise/`: `.runtime/`, `task/`, `sprint/`, `worktrees/`, and `database/`. The live Task DB is `database/tasks.sqlite`, its lock is `database/tasks.lock`, and backups belong only in `database/backups/`. Do not create compatibility files or symlinks for the old root-level database paths. This is the user's explicit local placement decision, superseding the external-state default for this project. Configuration is under `config/projects/ai-poise/`, separate from mutable data.

Discover and operate Task DB backups through the public `poise backup` CLI. Start with `poise backup help`; use `poise backup list --config PROJECT_JSON`, `poise backup create --config PROJECT_JSON`, and `poise backup restore --config PROJECT_JSON BACKUP_NAME` rather than copying or replacing the database manually. Run create and restore only while no agent or process is writing the Task DB. The complete operator contract is [Task DB backups](docs/task-db-backups.md).

The initial manifest has `push_required=false`, base ref `master`, and repository readiness `not_checked` because no initial commit existed at setup. Taskless bootstrap/verify do not establish Git/worktree readiness. Before repository-changing task work, verify that the configured base resolves to a commit and contains the required sources. A baseline commit needed to execute the assigned task is authorized by the task workflow; include only the approved project files and preserve unrelated changes.
