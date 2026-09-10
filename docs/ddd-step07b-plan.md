# HARNESS-DDD-07B — portable saved work

Started: 2026-09-07T00:25:38+05:00.

Goal: move saved work and its task-owned files/Git objects between two explicitly configured stores without manual database/file editing, preserving another task already present at the destination. The root and source AGENTS rules are updated separately as requested.

The first supported unit is an explicit batch of standalone tasks, or a complete published sprint and its members. A selected running task must first be handed off; the export tool may do that in the same packet for the current task. Active work owned by another actor is not silently released. Partial sprint imports, automatic merging/replacing existing tasks, migration and unattended remote delivery are not part of this slice.

The package contains a same-schema SQLite snapshot of selected owners, their complete task/sprint files, source Git objects and an integrity manifest. Source sessions/cursors and unrelated task data are not transferred. Destination configuration is explicit; no project settings or secrets are installed from an archive. Exact commands remain exact and must be executable in the receiving environment.

Test-first path: standalone WIP + section + message + artifact → export → import into a different root/repository already containing an unrelated task → bootstrap → verify → accept. Also: verified source evidence remains historical, private paths rebind without changing authored text, whole-sprint transfer, idempotent import, duplicate task conflict, corrupt/missing file rejection, preflight before side effects and rollback before business publication.

Implementation owners: TransferSpec/Manifest (pure validation), TransferCommands (batch orchestration through a port), scoped SQLite transfer repository, filesystem/Git adapter, existing WorkTools entry. Existing Task and Sprint repositories validate restored aggregates; transfer is not a generic raw-table API.

Sequence: contract/tests → observed RED → test review → implementation → GREEN → code review and direct regressions → docs/demo → full and delta archives. Live Gmail/IDE/installed-hook tests are not claimed by a local round trip.
