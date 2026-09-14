---
name: api-contracts
description: Define and verify exact producer/consumer interfaces with independent fixtures, explicit failure semantics and versioned evolution.
version: 1.0.0
---

# API and interface contracts

## Inputs

The affected HTTP, JSON, event, CLI, host callback, WebSocket or cross-application interaction; producer and consumer owners; canonical requirement; actual schema/version; authorization/idempotency/failure boundaries; and selected Poise project command/runtime configuration. Locate schemas/fixtures in their actual project owner from read-only worktree facts; Poise's routing/command mapping stays inside Poise. Obtain current Task/evidence context through [poise-workflow](../poise/SKILL.md).

## Procedure

1. Specify exact request, response, event or output schemas before changing both ends: required/optional fields, types, absence versus null, versions, ordering, errors, authorization, idempotency and evolution. Resolve ambiguity explicitly. Do not invent defaults, aliases, dual payloads or silent field substitution.
2. Assign one canonical schema/contract owner and identify every affected real producer and consumer. A copied DTO or shared serializer does not prove they agree. Preserve current compatibility requirements only when actually accepted; changing a public boundary needs an explicit migration/evolution decision.
3. Write independent test-owned input and expected output. Follow the [assertion policy](../../references/test-data-and-assertion-policy.md): canonical JSON **longer than 99 Unicode characters**, including newlines/control characters, moves to a file; exactly 99 may remain inline. Count canonical characters, not source bytes. Preserve contractual ordering and never generate expectations from production constants/serializers/algorithms.
4. Test the real producer boundary and the real consumer interpretation, including meaningful error/denial, malformed/absent data, duplicates or version conditions relevant to this interface. A mock asserting its own fixture is not consumer/provider coverage. Use the existing standard project tests and genuine integration boundaries rather than waiting for browser failure.
5. Keep fixtures, schemas and application tools under their existing project owners. Public serialization must honor its safe contract, without a mandatory trait, interface, runtime package or replacement serializer imposed by this skill. Do not expose private internal state solely to simplify tests.
6. Record requirement → schema/version → producer/consumer tests → actual evidence through existing Poise owners; no separate registry. Run selected boundary tests plus maintained smoke for ordinary completion. Build/browser evidence only when the plan actually needs that boundary and the operation is authorized.

## Output

The exact owned contract and explicit evolution decision, independent fixtures, producer/consumer coverage, command/source/runtime identity, actual targeted results and unresolved consumer or environment dependencies. Link these in the Task result and public handoff; static schema agreement alone is not end-to-end verification.
