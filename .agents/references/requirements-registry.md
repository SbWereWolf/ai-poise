# Requirements registry

English agent projection of the canonical Russian [registry contract](../../docs/workflows/requirements-registry.md)
and [migration rule](../../docs/governance/development-rules.md#миграции-реестра-требований).

## Independent application operations

`command`, `apply`, `rollback`, `squash`, `rebase` and `export` operate on the explicitly selected,
configured/associated application. No Task, claim, particular Task state, owner, stage, branch or
Task worktree cwd is required. The public composition does not construct a full Task runtime or
open/create its database. Each application keeps its own registry and history; no cross-application
merge or implicit resource redirection occurs.

`requirements_database` and `requirements_lock` resolve relative to `paths.state` when not absolute.
The explicit `requirements_schema_migrations`, `requirements_data_migrations` and
`requirements_schema_file` resolve relative to the loaded project configuration directory when not
absolute. Missing settings are errors, not Task defaults. Generated/replaced/deleted paths are
checked against configured data history/bootstrap resources. Runtime migration work uses the
configured state/runtime requirements directory. Read-only `query` creates neither registry nor
Task database; initialization is an explicit operation using the configured histories.

Registry requirements supply Task requirements. Before executable readiness/publication, preserve
the full agreed System → Application → Task lineage and immutable snapshot through the actual
Task/Sprint owner. Goal-only newborn drafts are not already accepted requirement chains. See
[agreement](../../docs/workflows/requirements-registry.md#декларативный-api-и-согласование) and
[historical snapshots](../../docs/workflows/requirements-registry.md#публикация-и-исторический-снимок).

## Usage

Use the shared versioned [command packet](../../docs/examples/requirements-taskless-command.json),
also referenced by the [Russian example](../../docs/workflows/requirements-registry.md#пример-записи-без-task).
Run the demonstration only on a separate configured application with an initialized Requirements
schema, empty data history, revision0, no sample IDs and unused migration name `0100-taskless`.
No Task database is needed. Do not run demonstration data against the live CE or Poise registry.
For real changes query actual rows, revision and history, agree exact values and choose a fresh name.

Select an interpreter with this Poise version available; `project_json` is the absolute demo project
configuration path and `packet_json` the absolute path of the shared packet:

```bash
"$python" -B -m poise requirements --config "$project_json" < "$packet_json"
```

Expected response: `status: "applied"`, `migrations: ["0100-taskless"]`. A public query packet
`{"operation":"query","input":{"queries":[{"id":"registry","kind":"registry"}]}}`
returns exactly the packet's two requirements and System/Application link, revision1. The same
query before the command returns empty requirements/links and revision0. Send public
`{"operation":"rollback","input":{"kind":"data"}}`, then query: empty rows/links, revision0.
Send `{"operation":"apply","input":{"kind":"data"}}`, then query: the identical rows/link,
revision1. Use the same CLI/configuration and stdin for all packets. The Task database remains
absent throughout. The example does not initialize a registry or safely repeat an existing name.

## Maintenance

The project-only `RequirementsProjectContext` supplies store, configuration, project fence and
runtime resources to `RequirementsMigrations`. `RequirementsCommands` holds the configured app
Requirements file lock once for a whole public mutation, under its project fence; internal operations
use standard yoyo schema/data backend locks and configured SQLite/lock wait budgets. There is no
single distributed transaction with Task DB. Concurrent failures require reading actual rows and
applied history before retrying; a migration filename is not an idempotent request receipt.

Preserve literal forward/reverse SQL, valid unique names, DML-only data history, FK constraints,
yoyo-owned transactions and applied/log/version history. No application imports, computed business
logic or SQL transaction control belongs in a data migration. On failure the current yoyo
transaction rolls back; earlier applied migrations remain. A failed command can leave its source
file: inspect it and history instead of blindly repeating the name. Correct unapplied SQL then
`apply`; do not rewrite published applied history. Apply schema before data; roll back data before
schema. Replay and rollback must preserve full rows and metadata, not merely row counts.

`TaskRequirementsMigrations` is a separate real completion adapter: retain Task context, ownership,
worktree, mutable-stage/file-scope checks, prepared-source handling and the normal completion cache.
It prepares Task-added DML relative to its Git baseline; even one file must be applied and verified
by rollback/reapply. No added DML means no generated migration. Public independent registry writes
do not waive these completion controls. A missing adapter returns causal
`requirements_migration_failed`, never successful no-op completion.

Yoyo determines migration order, dependencies and history. The private cleanup helper only
enumerates physical source files, including adjacent `.rollback.sql` companions, for squash/rebase;
it is not a public API, selector or history engine. Preserve effect order, unselected files and full
logical roundtrip checks. Rebase replaces obsolete data history for a new schema and refreshes the
single bootstrap; squash preserves sequential SQL rather than deriving a synthetic final state.

A public refusal reports its reason/recovery; read-only errors retain their query contract. Missing
configuration/resources require the actual configuration owner or explicit initialization; destination
errors require corrected configured paths. Task context/branch/scope errors belong to genuine Task
completion. SQL/constraint errors require inspecting actual rows and unapplied source. Lock errors
require the owner to finish and a retry. Exceptional manual yoyo recovery still needs exact object/action
authority, exclusion of other writers, bounded backup, audit and integrity/history/replay checks;
it grants no Task lifecycle/claim mutation authority. See canonical
[recovery](../../docs/workflows/requirements-registry.md#ошибки-и-восстановление).

## Change provenance

`POISE-REQUIREMENTS-INDEPENDENT-WRITE-20261117` establishes the independent contract and fixes
`AIP-REQUIREMENTS-PREREADY-WRITE-001`: readiness previously required a registry snapshot while
writing its prerequisites required an active Task/worktree. Git and fix evidence retain that history;
other active known limitations and Task records are unchanged.
