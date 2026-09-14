---
name: php-modernization
description: Modernize scoped PHP behavior using the actual supported version, explicit ownership and independent behavioral tests.
version: 1.0.0
---

# Scoped PHP modernization

## Inputs

The accepted language/design change, observable behavior, actual supported PHP version and dependency constraints, domain owner and selected Poise project commands/runtime. Read target-worktree Composer manifests, locks and architecture documentation as facts. Use [poise-workflow](../poise/SKILL.md) for Task/stage/allowed paths; store Poise routing/command/version configuration only in Poise.

## Procedure

1. Confirm the target PHP capabilities before choosing syntax/features; loading this skill does not authorize an upgrade. Prefer explicit types, constructor invariants, readonly/value objects where ownership warrants them, real domain enums, small boundary interfaces and analysis-visible shapes supported by that version.
2. Search for the canonical rule owner and preserve responsibility/dependency direction. Choose reuse/extract/separate based on semantics. Modernize only the assigned scope, not opportunistic unrelated files. Remove obsolete dual paths only when the accepted requirement replaces them; do not invent compatibility or discard a still-required contract.
3. Preserve behavior with independent exact tests at the real boundary. Use the [canonical JSON reference](../../references/json-serialization-testing.md) when the public representation is genuinely the contract. No mandatory JsonSerializable, JsonSerializeTrait, runtime package or replacement serializer is introduced through this skill or its references.
4. Keep expected values independent of production constants/enums/factories/configuration/serializers/algorithms. Preserve list order and contractual object order. Canonical JSON above 99 Unicode characters, counting newline/control characters, belongs in a file; exactly 99 may remain inline.
5. Use actual project formatter/static/architecture tools only where configured and applicable; do not mandate PHPStan/Larastan/Deptrac merely because ERP used them. Prefer available IDE semantic navigation according to project policy, with its documented fallback rather than repeated unavailable capability calls.
6. Inspect tests before implementation and code after GREEN under the existing two-role route. Run only changed-behavior/boundary checks plus maintained fast smoke for ordinary completion/integration; full regression is release-preparation work. Do not widen analysis baselines or test allowlists to make the change pass.

## Output

The scoped PHP change and placement rationale, supported-version assumptions, exact independent tests/fixtures, actual commands/source/runtime identity and focused results in existing Poise Task/evidence owners. State unresolved target environment checks honestly, then public handoff; self-review is not independent review or acceptance.
