# Task content and artifact ownership

The current [Poise work API](../../docs/workflows/batch-work.md#ответственность) owns stage results and their references. Bootstrap supplies the current template; [batch reads](../../docs/workflows/batch-work.md#пакетное-чтение) retrieve only the needed content, trace, evidence and feedback. File presence or a populated section is not proof of completion.

## Durable content

Keep requirements, plans, decisions, findings/resolutions, verification definitions and results in their existing Task/Sprint owners. Use public edit/ready, result, evidence and feedback operations rather than new task files, a copied database, a history registry or a changed-path list. Git supplies the actual source diff.

## Files

Create/register related artifacts in one batch through the [artifact API](../../docs/workflows/batch-work.md#создание-файлов). Use only the returned task/sprint/runtime roots and the configured scope directory. Stable task results belong to the current Task owner; session runtime is temporary. Do not guess paths, use symlinks to escape scope, or hand-edit a generated result file.

The API returns artifact identity and digest. Link the exact revision in the stage result. Existing files with different content are not silently overwritten; create a deliberate new version through the owner. Binary evidence is registered by its path where supported, not fabricated in a receipt.

## Completion boundary

Inspect required fields, links, executable checks and stage outcome through `verify`. Preserve failures and unavailable observations. A verified stage can still have an inconclusive or negative substantive outcome and is not user acceptance. Use the [existing handoff](context-handoff-contract.md), not a copied ERP folder layout or manual self-review/report ledger.
