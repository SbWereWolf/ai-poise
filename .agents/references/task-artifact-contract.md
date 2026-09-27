# Task content and artifact ownership

The current [Poise work API](../../docs/workflows/batch-work.md#ответственность) owns stage results and their references. Bootstrap supplies the current template; [batch reads](../../docs/workflows/batch-work.md#пакетное-чтение) retrieve only the needed content, trace, evidence and feedback. File presence or a populated section is not proof of completion.

## Durable content

Keep requirements, plans, decisions, findings/resolutions, verification definitions and results in their existing Task/Sprint owners. Use public edit/ready, result, evidence and feedback operations rather than new task files, a copied database, a history registry or a changed-path list. Git supplies the actual source diff.

## Files

Create/register related artifacts in one batch through the [artifact API](../../docs/workflows/batch-work.md#создание-файлов). Use only the returned task/sprint/runtime roots and the configured scope directory. Stable task results belong to the current Task owner; session runtime is temporary. Do not guess paths, use symlinks to escape scope, or hand-edit a generated result file.

The API returns artifact identity and digest. Link the exact revision in the stage result. Existing immutable files with different content are not silently overwritten. If an acceptance file must keep one working path across review/rework, declare it before first registration with [`artifact_drafts`](../../docs/workflows/batch-work.md#редактируемые-приёмочные-черновики). Submit its absolute path explicitly after each authorized same-path edit; release the Task before taskless `review` or `finalize`. Every review creates a separately registered immutable byte snapshot. Recover a premature immutable registration only through authorized taskless `recover` on a released nonterminal Task with exact ID/scope/path; do not create a `v7` workaround. Binary evidence is registered by its path where supported, not fabricated in a receipt.

## Completion boundary

Inspect required fields, links, executable checks and stage outcome through `verify`. Preserve failures and unavailable observations. A verified stage can still have an inconclusive or negative substantive outcome and is not user acceptance. Use the [existing handoff](context-handoff-contract.md), not a copied ERP folder layout or manual self-review/report ledger.

## Accepted duplicate-result delivery

Use the existing `reuse` operation for accepted permanent Task/Sprint artifacts from an explicitly related completed
source. The harness validates source registration, owner-relative identity, bytes and local delivery obligations before
acquisition, then uses the common non-overwriting publisher. Local references have the destination Task/Sprint owner;
source files and registrations remain unchanged. Verified local links and Task verification commit together. Retry the
original reuse request after an interrupted delivery; do not overwrite a conflicting file as a retry or manufacture a receipt.
Accept rechecks local bytes. Completed replay returns history, not restored files; use `recover_artifacts` for physical
recovery. Foreign runtime-session artifacts are not transferable permanent results.

Failed local verification or an unusable source deliverable is not a permanent block. Preserve WIP and use the existing
authorized restart to repair or regenerate the local result, then verify and accept normally. Do not edit the accepted
source or erase failed evidence. A saved pre-delivery candidate requests restart; completed replay remains historical.
