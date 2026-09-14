# Documentation impact checklist

Use this reference when accepted behavior, an interface, runtime wiring,
operator procedure, migration, test strategy, or repository workflow
changes. Record the result in the active Task's existing Poise content through its
owning API; do not create DOC-IMPACT.md or an ERP document registry.

## Locate the canonical owner

Check only the surfaces affected by the task:

- root and nearest application `README.md` files;
- relevant product requirements, architecture, testing, user, developer,
  and operator documentation under `docs/**`;
- application-owned runtime, deployment, migration, backup, recovery, or
  troubleshooting documentation;
- repository-tool manuals when scripts, task lifecycle, validation, or
  agent rules change;
- agent-facing references when a reusable execution rule changes.

Do not update historical task artifacts as though they were current
product specification. Do not copy the same rule into several canonical
documents.

## Outcome per document

For every candidate canonical document, record one of:

- `<path>` — updated: `<contract or behavior aligned>`;
- `<path>` — no change needed: `<concrete reason>`;
- `<path>` — not relevant: `<ownership or scope reason>`.

A bare `N/A` is not evidence.

## Consistency checks

- Commands, paths, versions, environment names, and screenshots match
  the implementation that was actually verified.
- Public payloads, validation messages, state transitions, permissions,
  and failure/recovery behavior match code and tests.
- Cross-links resolve and point to the canonical owner.
- Removed behavior is no longer documented as current.
- New user, developer, operator, migration, rollback, or recovery steps
  are discoverable from the owning document.
- Requirement, test, finding, and evidence IDs remain traceable where
  the repository contract requires them.
