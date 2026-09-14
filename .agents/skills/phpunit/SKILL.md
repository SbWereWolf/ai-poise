---
name: phpunit
description: Write PHPUnit tests with declared subjects, independent oracles, direct effect assertions and targeted project-owned execution.
version: 1.0.0
---

# PHPUnit contract tests

## Inputs

The requirement and observable result, declared unit/integration/acceptance boundary, actual PHP/PHPUnit version, current Task/scope and selected Poise command/runtime configuration. Read target test configuration and C029 language-checker declaration format as source facts. Use [poise-workflow](../poise/SKILL.md) for stage/evidence and [JSON testing](../../references/json-serialization-testing.md) for representation decisions.

## Procedure

1. **Every test file declares its tested classes** in the actual project/language boundary checker's supported format. Unit scope names its subject; integration scope names the real participating boundary; acceptance uses external entry points rather than production-symbol dependencies. Do not invent a parallel declaration schema or broaden it just to pass.
2. Choose the smallest test that can falsify the requirement. Define literal/test-owned expectations and meaningful positive/negative observations. Production constants/enums/serializers/factories/helpers/configuration/algorithms do not generate expected values. A typed production entry point is allowed to invoke the subject, not to construct its own oracle.
3. Prefer exact scalars/strings and complete safe public representations. No mandatory JsonSerializable, JsonSerializeTrait, runtime package or replacement serializer. Preserve list order and contractual key order; canonical JSON **strictly above 99 Unicode characters**, counting line/control characters, uses a fixture. Exactly 99 may stay inline. Normal tests never auto-update expectations.
4. Arrange/assert databases with parameterized direct SQL unless ORM behavior is the subject. Enumerate necessary fields, never SELECT * or alias.*. Invoke actual product behavior through its public entry point and assert the real persisted/output/exception effect. For other external systems use direct protocol-level or transparent test-owned infrastructure; the production wrapper cannot be both subject and oracle.
5. Obtain RED for intended behavior, not an import/environment/wrong-worktree error. **Before test handoff, run the language boundary checker and fix its violations in that same phase.** Missing checker/declared format is an explicit dependency, not a passing handoff. Do not use allowlist expansion to avoid a legitimate defect.
6. The independent reviewer inspects subject declarations/allowlists, true boundary and expected-value provenance beyond analyzer capability. Executor self-review and green static checks do not replace independent test inspection. Keep requirement/test/RED/GREEN/review links in current owners, not ERP registries.
7. Reuse actual owners for topology, file limits, cache and parallelism. Do not impose the former ERP 100-method limit, copy runtime wrappers or change the accepted cache key. Use the actual Composer/runtime entry and installed PHPUnit API; no forced upgrade or unsupported annotations.
8. Run only targeted behavior/boundary tests plus maintained fast smoke at ordinary completion/integration. Full suites belong to release preparation. Run relevant configured formatter/static/architecture guards without weakening assertions/baselines; preserve actual failure status and diagnose before rerun.

## Output

Tests with explicit subject declarations, independent fixtures, meaningful RED/GREEN, actual boundary-checker and reviewer evidence, exact effects and command/source/runtime identity in existing Poise owners. State missing review/check capabilities rather than certifying them, then public handoff; no self-acceptance or publication is authorized.
