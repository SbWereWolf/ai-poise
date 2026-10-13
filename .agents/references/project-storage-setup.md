# Project storage setup

Applies to creating a project or correcting its storage placement. Full operator
contract: [project roots and stores](../../docs/configuration/storage-layout.md#новый-проект-шаблоны-и-заполнение).
Supported generator packets: [project setup](../../docs/configuration/project-setup.md#пакетный-путь-для-агента).

1. Select the project, primary repository branch, explicit project data root and
   Task worktree directory inside the selected repository. The data root is the project folder;
   do not append a second `state` layer in the shipped layout.
2. Select the versioned blueprint through the configured registry with exact ID,
   version and digest. Use sanitized examples; keep filled operator settings out
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
5. Check the generated manifest and processes, registry selection, hook settings
   and actual consumer behavior. Keep a missing installation or unperformed check
   explicit. Create backups or archives only under separate user authority.

Use [infrastructure](../skills/infrastructure/SKILL.md) for operational changes and
[Poise development](../skills/poise-development/SKILL.md) when implementation changes.
