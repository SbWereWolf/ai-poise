# Project storage setup

Applies to creating a project or correcting its storage placement. Full operator
contract: [project roots and stores](../../docs/configuration/storage-layout.md#новый-проект-шаблоны-и-заполнение).
Supported generator packets: [project setup](../../docs/configuration/project-setup.md#пакетный-путь-для-агента).

1. Select the project, primary repository branch, explicit project data root and
   Task worktree directory through its explicit configuration. The data root is the project folder;
   do not append a second `state` layer in the shipped layout.
2. Select the blueprint by explicit ID through the configured path registry.
   Template/source version and digest pins are not required. Use sanitized examples; keep filled operator settings out
   of source commits. Preserve independently selected package versions.
3. Publish through the existing project owner. Verify the resulting Task,
   Requirements and telemetry paths; configure the separate hook registry through
   its owner. Bind runtime resources through the explicit bootstrap before mutable
   runtime use; native hooks also need an explicitly matching message source.
   Creation publishes configuration, not a data migration or native event.
4. For existing data, inventory writers and consumers before switching. Preserve
   foreign work and native history; validate actual bytes and references before
   retiring the old path. Parent/child relocation is rejected by the native mover;
   use only authorized audited recovery, not an automatic copy or blind retry.
5. Check the generated manifest and workflows, registry selection, hook settings
   and actual consumer behavior. Keep a missing installation or unperformed check
   explicit. Create backups or archives only under separate user authority.

Use [infrastructure](../skills/infrastructure/SKILL.md) for operational changes and
[Poise development](../skills/poise-development/SKILL.md) when implementation changes.

## Root-placeholder examples

Use the [canonical poise-op-derived layout examples](../../docs/configuration/storage-layout.md#образцы-размещения-на-основе-poise-op).
Uppercase `${...}` markers require explicit textual replacement before use;
Poise does not automatically expand them. Select source, configuration, data,
repository, setup, launcher and common-filesystem roots independently. Preserve
native lowercase owner variables and relative-path contracts. Existing hook/editor
relative tails illustrate the selected installation and need explicit replacement
for a different layout.

The two existing project blueprints propose the same poise-op-derived placement.
Current template/source files are read without mandatory version/digest pins.
Build all path values through explicit native edits; the hooks list is one
whole-field edit. Creation publishes workflow files to their configured paths,
including outside the configuration directory and setup root. Absolute paths
are used as supplied; relative paths resolve from the final manifest directory.
The registry is published last. A transient pending marker supports continuation;
no permanent creation receipt or provenance hash is needed. Registered repetition
reads current operator-edited files and runs fresh selected readiness probes.
Pending continuation reads existing graphs and needs current sources only for
missing graphs. Preserve locking, schema/readability checks and real file/directory
collision protection. See the canonical [publication contract](../../docs/configuration/project-setup.md#публикация-и-повтор).
Hook template uppercase markers also require explicit filling before native use;
lowercase Python/source/workspace strings are preserved, but current hook settings
are explicit inputs; do not claim automatic template rendering without an actual
consumer. Blueprint questions cover all scalar paths; runtime_resources.hooks
is an explicit whole-list batch edit because question types cannot represent it.

## Editable Task and workflow starters

Follow the [current starter contract](../../docs/configuration/workflow-catalogue.md#редактируемые-исходники-и-сохранённые-задачи).
New source selections contain only id. Workflow source entries require path;
Task entries require path and goal_type. Read current valid source content without
mandatory version/digest pins. Materialized Tasks use their saved contract after
source changes or removal. Planning keeps null or an explicit id with optional
historical version/digest text; preserve those audit strings without source lookup.
Keep Task/API unknown fields strict, current goal identity, request idempotency
and optimistic publication checks. Generic workflow create may derive a new goal
from a different source goal. Do not claim pin removal repairs malformed stages.
