---
name: tdd
description: Derive the smallest falsifying test from a requirement, inspect its independent oracle, and preserve genuine RED/GREEN evidence.
version: 1.0.0
---

# Test-driven behavior changes

## Inputs

A canonical requirement, one observable outcome, the real domain/integration boundary, the current stage/allowed paths and the project-owned command/runtime selected by Poise. Load the [test data and assertion policy](../../references/test-data-and-assertion-policy.md) for oracles, fixtures and boundaries. Documentation-only non-executable changes do not need artificial RED; check their paths, links and executable claims instead.

## Procedure

1. Identify what observation would disprove the required outcome. Choose the smallest standard test that can actually observe it: unit for isolated behavior, integration for real wiring/persistence, external entry-point/browser for externally visible interaction, architecture checks for dependency contracts. Raise the level when a lower test cannot observe the requirement; excessive mocks cannot substitute for the owner.
2. Prepare exact, independent expectations and a transparent test arrangement. The production algorithm, constants, enums, factories or serializer must not generate the oracle. Keep C029 declared subject/allowlist boundaries honest; never widen an allowlist or baseline merely to make a guard pass.
3. Obtain RED for the intended missing behavior. Wrong worktree, import/dependency failure, unavailable service, syntax error and unrelated failures do not demonstrate that behavior. Preserve exact command, source/runtime identity and failure evidence; diagnose infrastructure separately.
4. Inspect the test before implementation. Executor self-review checks sensitivity, scope, positive/negative cases, data independence and meaningful failure; the distinct reviewer performs the independent inspection required by the two-role route. Self-review or a static guard is not independent inspection, and unavailable review must be reported rather than impersonated.
5. Implement the smallest complete behavior inside the allowed paths. Pass the focused test, then refactor while preserving the semantic contract. Do not weaken exactness, drop negative scenarios or replace a failing expectation with generated output.
6. Record requirement → stable test/observation → RED → inspection → implementation → GREEN → code inspection links through current Task/content/evidence owners. Use [Poise evidence](../../../docs/workflows/evidence.md#один-пакет-результата), not TEST-SPEC/SR/RFR registries or restored C059/C065 workflows. A changed requirement requires an explicit accepted decision, not a convenient assertion edit.
7. Retire only a temporary test whose sole purpose was observing an obsolete implementation mechanism, and only after permanent behavior/contract/architecture evidence proves the replacement. Keep meaningful negative contracts, forbidden side effects and regression scenarios even when they describe absence.
8. Run targeted regression plus maintained fast smoke through [direct-checks](../direct-checks/SKILL.md); save real results and limits. Ordinary Task completion/integration never falls back to the full suite. Keep task-local diagnostic configuration local and non-production; do not turn it into a machine-wide or production default.

## Output

The test and independent oracle, exact requirement/test/RED/GREEN/inspection links, changed behavior, focused regression results and remaining gaps in the managed Task result. Fixtures above 99 canonical Unicode characters are test-owned. No mandatory JsonSerializeTrait, JsonSerializable implementation or runtime serializer dependency is introduced. SQL explicitly names necessary columns: neither SELECT * nor alias.* is permitted.
