---
name: repo-tooling
description: Implement repository tools through existing owners with explicit inputs, bounded output, truthful failures and idempotent recovery.
version: 1.0.0
---

# Repository tooling

## Inputs

The authorized tooling behavior, actual owner/API and protocol, current Task/stage/allowed paths, explicit filesystem path of the code under work, relevant target-project facts and selected Poise configuration for commands, versions, runtime and C004 routing. Use [poise-workflow](../poise/SKILL.md) and bootstrap rather than inventing command, identity or state. The tool contract states input schema, validation, output schema, effects and exit statuses before implementation.

## Procedure

1. Locate the existing domain owner, adapter, command or generator and extend it rather than creating a parallel lifecycle, Task reader, schema, runner, profile store or telemetry engine. Target-project manifests/docs are read-only facts; Poise configuration remains inside the selected Poise project. Repository tooling implements adapters/mechanics, not replacement planning or execution semantics.
2. Design contract tests for success, invalid input, absent capability, partial failure and replay. Declare the atomicity boundary and stable operation identity where required. Repeating an idempotent operation preserves semantics without duplicate rows, messages, files, commits or downstream effects. A retry after an ambiguous failure establishes the completed effects before continuation.
3. Work only in the returned Task worktree and explicit paths. Preserve unrelated dirty files, staged content and foreign worktrees. Use owning APIs and batch independent reads/writes; honor optimistic versions, lock ownership and existing transaction/recovery state. Do not directly edit managed Task DB or lifecycle.
4. Keep execution, parsing and presentation separate. Capture real process exit status and complete durable stdout/stderr before parsing. A parser, truncation, empty output, missing summary or optional formatter must never turn command failure into success. Malformed output is a diagnostic failure, not a default green result. See [command-output policy](references/command-output-policy.md).
5. Keep the owner as the only active-run observer. Use its supported terminal result/cancellation/recovery and runner-owned C033 timing policy; do not hard-code or extend a limit without progress evidence and the owning policy. Current Poise verification methods declare no per-method timeouts; do not add one as a tooling shortcut.
6. Treat optional C028 telemetry as a separate non-blocking path: preserve main operation status/results even if telemetry fails. Record an available bounded telemetry diagnostic without requiring another telemetry implementation or allowing telemetry to drop correctness-critical business evidence. Main work failures remain failures.
7. Follow [runtime and shell policy](references/runtime-and-shell-policy.md): pass argv separately, resolve the selected interpreter and working directory, preserve exit status, and protect secret inputs. No imported ERP entry points or Windows/MSYS2 bootstrap mechanism is retained. Do not silently replace a configured runtime with host tooling.
8. Verify the changed behavior and boundaries with targeted tests and the maintained fast smoke; full suites belong only to release preparation. Exercise a second run and interrupted-run recovery where idempotency is required. Inspect tests and code under the existing two-role process; self-review does not count as independent review.
9. Update the owned instruction/example/schema that changed. Use [rule ownership](references/agent-rules-architecture.md) and [bounded context](references/agent-prompting-and-token-policy.md) rather than copying a document-read registry or adding a second policy owner. Check all directly affected links and executable claims.

## Output

The implemented tool contract and focused tests, exact input/result/exit/error behavior, replay/recovery evidence, source/runtime identity and bounded diagnostic links in the existing Task/evidence/artifact owners. Link remaining external capabilities, not invented successes. Transfer via public handoff; this skill does not authorize acceptance, master integration or publication.
