# Context and handoff

Use the [ordinary release packet](../../docs/workflows/local-handoff.md#пакет) and
[direct role handoff](../../docs/workflows/local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим).
A conversation summary is not storage, release, acquisition or acceptance.

## Required context

Preserve project/Task, current stage/role, exact source/result identity, actual allowed paths,
requirements, findings/resolutions, full check/evidence links, blockers and next authorized action.
Include the stable progression request/target, omitting the target when that is the actual intent.
Link authoritative current sources instead of copying whole specifications.

## Transfer

Save results, artifacts and findings through their existing owners before ordinary `release`.
Read the current Task version; supply task_id/request_id/expected_version/reason. Release neither
submits a result nor commits, copies, verifies or accepts work. Confirm ownership_released before
messaging the known counterpart. The recipient acquires the same Task through its own bootstrap
and continues in the existing working area with preserved WIP; no transport or bundle is created.
Existing authorization covers ordinary continuation, not unrelated work or controlled publication.

A failed release leaves responsibility with the sender. A message cannot repair it. After successful
release, retry failed notification against the same saved result without silently reclaiming work.
Do not impersonate a reviewer or caller. Preserve incomplete material and actual unknown outcomes.

## Resume

Use the same authoritative Task state and source/evidence identity. Inspect actual gates and pending
effects before continuing. Do not merge stores, reconstruct a second lifecycle, copy another
participant's work area or edit managed state. Removed handoff/transfer Work operations are not a
fallback. Scope and authority remain with the current public owners.
