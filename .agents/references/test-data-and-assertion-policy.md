# Test data and assertion policy

## Exact contracts and independent expectations

Tests remain sensitive to accepted behavior, declared interfaces, persisted values, messages, transitions and safe serialized representations. Do not weaken assertions to reduce maintenance. A behavior-preserving refactor keeps semantic tests green; a structural assertion changes only with an explicitly accepted contract change recorded through the existing Task owner.

Expected values are literals or test-owned constants/data, never derived from the production algorithm, constants, enum values, factory, configuration provider, serializer or helper also being tested. A production type may be necessary to invoke a typed entry point; it must not determine the expected answer. A test whose subject is the enum/constant/serializer may inspect that subject directly but still states the expected contract independently.

## Canonical fixture threshold

Use a fixture when the exact canonical input or expected output is **strictly longer than 99 Unicode characters**. Exactly 99 characters may remain inline. Count the normalized canonical string used by the assertion, including newline, tab, escape and other control characters; a control character alone does not force a fixture. This threshold is characters, not UTF-8 bytes and not a rounded 100-character cutoff.

Fixtures are UTF-8, diff-friendly, test-owned and never auto-updated by a normal test run. Prefer exact scalar values, then a complete safe canonical JSON/XML/HTML/text contract, then a fixture for that canonical value above 99 characters. Sort object keys recursively only where ordering is not contractual; preserve array order and all contractual ordering. Do not inspect framework wrapper internals as a substitute for observable behavior.

## Safe public representation

Compare the real public behavior or an explicitly declared safe serialization boundary. There is no mandatory JsonSerializeTrait, JsonSerializable implementation, Composer/runtime package or replacement serializer. Tests do not force production objects to expose fields solely to simplify assertions. Do not serialize secrets, credentials, handles, proxies, cycles or unsafe internal state. When serialization itself is the contract, invoke its real producer and compare exact independent test-owned output; test consumers at the actual integration boundary as required.

## Database arrangement and assertions

Unless ORM behavior is the declared subject, arrange/assert through parameterized direct SQL, not production models, repositories, factories, scopes, casts or helpers. Invoke application behavior through its real public entry point. Verify exact rows/counts/values/constraints and ordering only where specified. Select necessary explicit columns; SELECT * and alias.* are forbidden. Direct test SQL is not permission to mutate managed Poise Task DB or lifecycle state.

## Scope and language guards

Unit tests depend on their declared subject and test-owned infrastructure. Integration tests depend only on the explicitly declared integration boundary. Acceptance tests use external entry points/infrastructure, not production symbols. Values originate in literals, an explicit test dataset, migrations, a dump, or an explicitly authorized production-backup exercise; they are not reconstructed from production rules.

Use C029 language-specific subject declarations/allowlists and the existing checker where applicable. Never widen them to pass a guard. Checker success does not establish oracle independence or honest scope: executor and independent reviewer inspect those properties. Use direct protocol-level test infrastructure or transparent test-owned helpers for external systems; the production wrapper cannot be both subject and oracle.

## Inspection and evidence

Inspect tests after meaningful RED and before implementation under the existing two-role route. Self-review is not independent inspection. Preserve requirements, test IDs, RED/GREEN, review and accepted expectation changes through [Poise content/evidence](../../docs/workflows/evidence.md#один-пакет-результата); do not create TEST-SPEC/SR/RFR registries. Keep permanent negative contracts. Remove temporary obsolete-mechanism tests only after permanent replacement behavior evidence exists. Targeted checks plus maintained smoke are the ordinary completion/integration boundary; full suites belong only to explicit release preparation.
