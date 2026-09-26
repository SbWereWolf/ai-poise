# Poise V1 local-agent continuation point

This repository is the source portion of the final Poise V1 handoff.

## Source state

- Product version: `1.0.1`
- Cleanup commit before handoff: `e3500ef8c920e49a16fd6be2ea11b8b206891c6a`
- Branch: `maintenance/v1.0.1-sprint-role-snapshot`
- The obsolete frozen-task role-projection migration has been removed.
- Do not resurrect or continue the abandoned snapshot-migration approach.

## Operator state

The handoff package carries operator configuration and persistent state separately from this Git repository:

- `operator/profile/`
- `operator/state/`

All SQLite files in the handoff were checked with `PRAGMA integrity_check` before packaging.
The main state contains the existing 96-Task Sprint snapshot. The intended next product workflow is to create a new single-role process graph and recreate tasks from the preserved planning material rather than migrating the old frozen workflow snapshots.

## Planning material

See the handoff package `planning/` directory for:

- single-role process acceptance evidence;
- single-role process pack draft;
- Sprint recreation plan/design;
- Sprint 002 draft;
- retained regression evidence.

## Recovery

Use the `workspace-recover.manifest.json` and the bundled workspace-recover tool from the final handoff package. Do not reuse archived virtual environments; recreate dependencies/runtime locally.
