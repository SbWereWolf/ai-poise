# AI poise source development

Updated: 2026-09-12T04:43:01+05:00.

## Declarative, reusable tools

Whenever a required workflow contains two or more mechanical actions with no reasoning decision between them, provide one tool that performs the sequence.

Make every tool batch-oriented and declarative. The agent describes the desired result and related changes in one logical request; the tool ensures that the result is applied through the owning APIs. Do not require one call per item or manual supervision of bookkeeping.

Do not duplicate existing tools. Compose their calls or simplify their inputs and result representations. Keep native IDE, Git and coding capabilities where they already provide the needed operation.

## Domain ownership

Apply domain-driven design throughout the codebase, not just to stage handlers. Identify the owner of each business rule before adding behaviour.

Document and respect the responsibility boundaries of libraries and data owners. Change Task, Sprint, content, evidence, configuration and artifact state only through their owning APIs. CLI adapters, runners and hooks must not bypass those APIs with direct lifecycle assignments or table updates.

For every new trace requirement, ensure that its due stages intersect the referenced point's `write_stages`. Apply this rule to new goal-type and Task candidates, Sprint publication, and new active-Task additions before persistence or external effects. Restore stored contracts without retroactive rejection or rewriting, but validate every genuinely new requirement added to them. Keep immutable early evidence writable only at its owning stage; include that stage among the requirement's due stages instead of making the evidence writable later.

Keep domain code independent of I/O. Application services coordinate domain objects and ports; infrastructure implements those ports. Reuse transaction, execution and presentation mechanics without creating a universal raw-data editor.

Reuse the standard stage handlers and the common route runner across workflows. A new goal type defines its own process; it does not require a new execution engine.

## Explicit configuration

Do not embed literals that determine the workflow, task format, acceptance conditions or observable result in application logic. Supply those choices through explicit configuration. Internal implementation constants may describe mechanisms, but must not silently select business behaviour.

AI poise must be configurable without editing its source code. Missing required configuration is an error: do not supply hidden defaults, fallback values or guessed settings.

Do not preserve backward compatibility merely to read earlier formats. Do not design or run data migrations without a direct user instruction; request permission when a migration is necessary.

Each goal type has its own complete, self-contained process configuration. Define its task template and rules for creation, stages, checks and completion. Reusing library code does not imply inheritance between goal-type business configurations.

Keep all AI poise configuration in the AI poise codebase and select the project explicitly. Do not infer executable commands from a target application's prose instructions; register the exact invocation in the task or project configuration.

## Development workflow

Use a dedicated Git worktree and the repository branch-naming rule before modifying code. Do not complicate read-only inspection with worktree creation.

Follow the [TDD rules](../docs/governance/development-rules.md), [library boundaries](../docs/architecture/boundaries.md) and [declarative tool contract](../docs/architecture/declarative-tools.md). Write and inspect tests before implementation, verify the completed path, review fixes, and update tool, code and storage documentation with a timestamp.

Never run the full test suite during task work, including at a delivery boundary. Run only narrow task-specific checks and the maintained bounded `tests/smoke.sh`; do not register unfiltered repository-wide test discovery as a task method.

Every new Task worktree starts from the current configured base ref observed for that start. Sprint result dependencies expose predecessor commits through `result_provenance`; they do not select or merge a successor branch base.

Every new verification method must declare `source_under_test`. Bind repository sources to
paths inside the current task worktree; never infer a language layout, overwrite an existing
environment value, or add a fallback checkout. External methods require an explicit reason.
Verification methods never declare execution timeouts; after launch, wait for the process to
reach a terminal result. Capability probes, hooks and other bounded infrastructure retain their
own explicit timeout contracts.
Reject semantic duplicates and conflicting methods before execution. Accept RED evidence only
when the declared failure predicate matches and the recorded source provenance is valid; an
import error or execution against another checkout is not a valid RED.
