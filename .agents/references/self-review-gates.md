# Self-review checkpoints

Self-review belongs to the current executor or reviewer. It is not independent review, acceptance, another role or a user-stop ceremony. Store its compact result through the current [verify contract](../../docs/workflows/batch-work.md#verify); do not create manual identifiers or parallel reports.

## Test work

Before implementation, establish the requirement, real observation boundary, intended RED, and independent expected values. Exclude syntax, fixture, environment, import and wrong-worktree failures from RED. Test-owned literals and constants may define expectations; production algorithms, enums, factories, helpers and serializers may not generate them. A language-boundary allowlist names the actual subject, not arbitrary dependencies needed to silence the checker.

Canonical JSON over 99 Unicode characters, including newlines and control characters, belongs in a fixture. Exactly 99 remains eligible inline. Compare the specified structure and meaningful order without requiring any serialization trait/interface/package. For a non-ORM subject, prepare and inspect database state with parameterized SQL; invoke the behavior through its real application entry point. Demonstrate sensitivity to a realistic fault without requiring a full mutation suite.

## Product work

Inspect the complete Task diff. Record searched behavior/contracts/tests/callers, candidate implementations, and the reuse/extract/separate decision. Name the canonical owner, how consumers reach it, and why intentionally separate behavior is not the same contract. Check relevant negative states, transactions, security boundaries, migrations, source/build identity, documentation and artifacts required before the next gate. Do not impose an unrelated stack or infrastructure topology.

## Remediation

Read every original finding and resolution. Map the corrections and current evidence to the same IDs and criteria. Confirm tests and allowlists were not weakened. Use targeted checks plus maintained fast smoke; submit the complete resolution batch through the existing FeedbackBook route. Newly discovered defects are separate facts, not implicit scope expansion.

## Reviewer results

Check your own findings for a confirmed obligation, condition/consequence, exact revision, reproducible evidence, correct producer stage and verifiable correction criterion. Correct false positives and distinguish protective runtime rejection from a product defect. Independently inspect every proposed resolution, but never fix product code in the inspection stage.

## Before handoff

Preserve inspected scope, findings, fixes, evidence, limitations and required artifacts. A material unresolved self-review defect blocks a successful handoff claim; a saved incomplete result may be released with an explicit blocker. Use only the [existing release protocol](../../docs/workflows/local-handoff.md#передача-между-исполнителем-и-ревьюером). No skipped-role records, manual registries or publication authority are created here.
