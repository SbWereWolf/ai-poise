# Canonical JSON testing without a production serializer mandate

## Boundary first

Test the safe public behavior or explicitly declared serialization contract. There is **no mandatory JsonSerializable implementation, JsonSerializeTrait, runtime Composer package or replacement serializer**. A test must not force private objects to expose every field to make assertions easier. A project may already own a serializer for real product requirements; use its public boundary when that behavior is the subject, not because this reference mandates it.

Do not serialize secrets, credentials, runtime resources, closures, proxies, cyclic graphs or otherwise non-public state. Use a safe declared DTO/value/public-behavior observation instead. A changed public representation needs an accepted requirement/evolution decision linked in current Task content; no separate refactor registry is introduced.

## Canonicalization

The test-owned canonicalizer reports encoding/decoding failure instead of comparing a silent fallback. Work with JSON-compatible values, recursively sort object/map keys only where key order is not contractual, preserve list/array order and use one explicit escaping/indent/final-newline policy. Compare exact strings, not an incomplete field subset when the complete safe representation is the contract.

Expected output is an independent literal/test-owned fixture, never derived from production constants, enums, serializer output, factories, configuration or the same algorithm. Canonicalizing a fixed independent literal is different from generating the expected answer by executing the subject.

## Exact fixture threshold

Store the canonical input/expected output in a UTF-8, diff-friendly file when it is **strictly longer than 99 Unicode characters**. Exactly 99 may remain inline. Newlines, tabs, escapes and other control characters count in the canonical string used by the assertion; they do not independently force a fixture. Count characters, not bytes. Normal tests never update fixtures automatically.

## PHP and frontend values

PHP tests use actual supported throwing/checked JSON APIs without requiring production serialization changes. A public serializer's behavior test may call that serializer, but the oracle remains independent. JavaScript/TypeScript tests compare JSON-compatible values, preserving list order and excluding Vue wrapper internals, DOM/browser objects, functions, Symbols and cycles. Real producer/consumer tests are needed when the interface crosses owners; common helpers alone do not prove agreement.
