# Poise V1 local-agent handoff

## Continue from here

- Product version: **1.0.1**
- Git HEAD: **72dc2851d5f12c0005c32ec3da867777ba60dc4d**
- Parent cleanup commit: `e3500ef8c920e49a16fd6be2ea11b8b206891c6a`
- Branch: `maintenance/v1.0.1-sprint-role-snapshot`
- Worktree is clean; no push was performed.
- Obsolete frozen-process role-projection migration code was removed before handoff.

## Persistent operator state

`operator/profile/` and `operator/state/` are included. All included SQLite databases are verified with `PRAGMA integrity_check`; the main state contains 96 Tasks and 1 Sprint.

The archived virtual environments/runtimes/dependency caches are intentionally excluded. Recreate Python/Node dependencies locally.

## Product direction

Do **not** continue the abandoned workflow-snapshot migration approach. The agreed next workflow is:

1. use/design the single-role process graph;
2. recreate the Sprint/Tasks from preserved planning content rather than migrating old frozen workflow snapshots;
3. preserve content/requirements/dependencies, but create the new Tasks against the new single-role process/template;
4. continue implementation from the recreated Sprint.

See `planning/` for the process pack, acceptance evidence, Sprint recreation plan/design and Sprint 002 draft.

## Restore

After extracting this handoff archive:

```bash
python3 recovery/recover.py --target /absolute/path/poise-restored
```

Or run the attached workspace-recover manifest directly from the extracted handoff root:

```bash
node recovery/workspace-recover/bin/workspace-recover.mjs flow run \
  --manifest workspace-recover.manifest.json \
  --session /tmp/poise-handoff-session \
  --workspace .
```

The manifest creates `restored/`, relocates environment-specific paths, runs `git fsck`, checks clean HEAD, verifies all SQLite databases and confirms 96 Task / 1 Sprint in the main state.
