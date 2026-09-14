# One local reference source

The guide bodies in `guides/` are the 138 files supplied in the ERP archive, with 136 preserved byte-for-byte and two navigation-only Markdown repairs (an explicit anchor and a joined heading), listed in `SNAPSHOT.json`. Its source skill declares snapshot `2026_05_16-c5e78707`; the source lock identifies `GoogleChrome/modern-web-guidance`, skill path `skills/modern-web-guidance/SKILL.md`. `SNAPSHOT.json` records that provenance and every source/local guide SHA-256. This is not a claim that the snapshot is current, that the upstream commit was independently authenticated, or that a current target browser supports every example.

`catalogue.ndjson` is a local path/title/category index with bounded excerpts from those files. `categories.ndjson` summarizes bounded discovery choices. Both are generated from the same guide set; they are not a second guidance source. Use local search on Linux/WSL and batch selected reads without installing an npm CLI.

## Authorized update procedure

1. Open/receive an explicitly scoped library-update Task. Select an exact upstream repository revision or immutable release artifact; record its identity and digest before importing. Do not resolve an unknown `@latest` during ordinary lookup.
2. Import one complete, reviewed snapshot into this skill's `guides/` boundary while preserving upstream attribution/license material. Inspect changed guidance and target compatibility separately; do not silently mix old guides with new remote search/retrieve output.
3. Regenerate the catalogue and category descriptions from that same snapshot; update source revision, member hashes and provenance in `SNAPSHOT.json`. Preserve the old snapshot through Git history, not duplicate active trees.
4. Check every indexed path/hash and direct local reference; exercise bounded local search and selected reads offline. Validate changed API examples against the intended installed runtime and actual browser policy. A link check is not browser compatibility validation.
5. Save the diff, source identity, checks and limitations through existing Poise Task/evidence, then use its independent review/handoff route. Updating the library does not authorize target dependency upgrades or deployment.

No network call is required per lookup. A future update may require authorized retrieval of the exact chosen artifact, but the source identity must remain explicit and reproducible.
