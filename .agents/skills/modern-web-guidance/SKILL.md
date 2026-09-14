---
name: modern-web-guidance
description: Search the pinned local modern-web guide snapshot for a concrete scoped platform/layout question, without mandatory network calls or skill trios.
version: 1.0.0
---

# Local modern web reference library

## Inputs

A concrete action-oriented web/platform/layout question, current Task/stage/scope, actual browser/runtime compatibility policy and selected Poise routing/command configuration. Use [poise-workflow](../poise/SKILL.md). The local [source manifest and update procedure](SOURCE.md) and `SNAPSHOT.json` identify the guide snapshot; it is a reference source, not proof of current browser support.

## Procedure

1. Route through the existing C004 owner only when the current question needs this guidance. Do not load a mandatory layout/Tailwind/web trio or read every guide. Target-worktree configuration/docs are facts; Poise routing/browser mapping stays inside Poise. An unrelated backend, store or routine class change does not need the library merely because it is web-adjacent.
2. Search locally first using the task question and distinctive action/symptom terms. `catalogue.ndjson` contains guide IDs, category, title, bounded description and path, not full bodies. From this skill directory, for example: `rg -i -m 8 'keyboard|focus|dialog' catalogue.ndjson`. This is a local text search, not semantic similarity scoring.
3. Refine an empty/weak query with domain synonyms and intended outcome. Only then inspect a bounded set of category names/descriptions from `categories.ndjson`, choose the relevant category and narrow the catalogue again. Do not dump all guide bodies or replace a weak search with an unbounded network list.
4. Batch-read only selected existing guide paths/ranges through the available content-read owner. Follow necessary sections to completion for the decision; a bounded preview is not complete inspection. Preserve the guide path and snapshot identity in Task evidence. Do not hallucinate IDs or imply the file was read merely from its title.
5. Adapt the guidance to the actual target framework/version/browser contract. Snapshot Baseline/support dates and examples are not current compatibility measurements. Confirm version-sensitive facts with the actual runtime and primary specifications/documentation when needed. Explicit target browser/fallback policy takes precedence over a generic guide recommendation; do not add polyfills or change that policy silently.
6. Use the single explicit update procedure only for an authorized library update. Ordinary lookup works offline on Linux/WSL and requires no npm invocation. Never mix this local snapshot with unknown `@latest` output, require network for every question or retain Windows `.cmd` workarounds. A missing library/file is a concrete setup dependency, not permission to swap sources silently.
7. Apply only the selected material to the scoped design/implementation/review decision. Run relevant targeted checks and maintained smoke where work changes behavior; use the configured browser for actual visual interaction. Do not turn documentation into unmeasured performance/browser support claims.

## Output

Selected guide IDs/paths and snapshot identity, the concrete decision and adaptation to the target policy, relevant focused evidence and unresolved compatibility assumptions in existing Poise Task/content/evidence. The library supplies reference material; it neither implements automatic routing nor authorizes upgrades, deployment or acceptance.
