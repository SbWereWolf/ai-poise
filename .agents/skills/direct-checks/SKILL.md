---
name: direct-checks
description: Select and preserve targeted behavior/boundary checks plus maintained fast smoke without full-suite fallback during ordinary work.
version: 1.0.0
---

# Targeted verification

## Inputs

The exact owned diff, accepted requirement and observable boundary, current Task/stage/evidence plan, reusable evidence identity, selected Poise project commands/runtime/tool versions and current impact/routing result. Read target-worktree manifests and documentation as facts; do not store Poise configuration there. Use [poise-workflow](../poise/SKILL.md) for the actual executable API.

## Assigned skills and missing expertise

Use this execution method together with the subject skills assigned to the current
stage. It does not replace knowledge of the checked behavior or test framework.
Follow the [stage-skill draft and gap-reporting rule](../../../docs/workflows/task-stage-skills-draft.md#использование-навыков-и-сообщение-о-нехватке):
report a missing/insufficient skill, the affected stage, impact and proposed planner
action in the final answer. Do not claim the planned template fields are supported,
change the planner's assignment silently, or treat missing tooling as product RED.

## Procedure

1. Resolve checks against changed behavior and real interfaces. Preserve existing C004 selection, C016/C017 impact/evidence ownership and the unchanged C018 cache key. This skill does not reimplement their planner, runner, recorder or cache. Missing/ambiguous target selection is a diagnostic dependency, never permission to select all tests.
2. At ordinary Task completion and result integration, run only required targeted checks plus the maintained fast smoke. Full or unfiltered regression suites are prohibited there and reserved for explicitly authorized release preparation. Even a broad suite named "fast" must not smuggle unrelated acceptance/E2E/release scenarios into ordinary work.
3. Use application-owned standard tools with the exact filesystem path or `cwd` required by that check. Do not invent task-local replacement runners, copy another project's shell wrappers or silently use host tools in place of configured containers. Language static/architecture checks run when the actual change crosses those contracts, not because an ERP project used them.
4. Reuse only terminal evidence whose source, fixture, command/configuration and runtime identity still satisfy the existing owner. Preserve its cache semantics and key; a cached result outside its declared inputs is not proof of the current external state. Never broaden cache inputs ad hoc or manually copy check evidence into a Task.
5. Obtain terminal result, full saved output and actual exit code from the runner. Only that owner continuously observes an active run. Diagnose failures via [debugging-and-recovery](../debugging-and-recovery/SKILL.md); no blind repeat, hidden suppression, broad fallback or selective reporting.
6. Run build, direct SQL, browser, deployment or production-like evidence only when the real boundary/acceptance plan requires it and the action is authorized. A UI text change does not automatically grant deployment authority. A smoke pass cannot replace the required focused assertion; a focused unit pass cannot prove a real integration or served build.
7. Use the project's existing formatters and owning package scripts on changed files after substantive corrections. Do not install foreign framework tooling, auto-update expected snapshots or widen analyser baselines to get green output. Keep genuinely unresolved obligations visible.
8. Submit check references with source/tree, runtime and built/served identity where applicable via existing [evidence](../../../docs/workflows/evidence.md#актуальность-и-повтор) and stage verify. A launcher exit code or an accepted receipt is not a terminal check result; a verified stage is not user acceptance.

## Output

A bounded check plan, exact selection rationale, reused/new terminal evidence, real command/exit/output/source/runtime identities and explicitly unavailable obligations. No full repository suite was implicitly selected. For AI poise itself, the maintained ordinary smoke entry is `bash tests/smoke.sh <selected-python>`; resolve the interpreter from the installation rather than copying a workstation-specific path. Full regression belongs only to a separately authorized release-preparation assignment.
