---
name: vitest
description: Vitest and Vue Test Utils for SFC, Pinia, Router, composable, frontend-domain, adapter, and migration characterization tests with exact oracles and self-review.
version: 4.2.0
---

# Vitest and Vue Test Utils

Resolve test parallelism, cache, topology, runtime and commands through the
selected Poise project and the target's accepted verification plan. Target
settings are source facts, not a location for Poise configuration. Split test
files by independent behavior; do not import ERP audit paths or invent a new
local variant of the project's test contract.

Load this skill only when writing, changing, diagnosing, or running Vue
or JavaScript/TypeScript non-browser tests. Do not load it for Vue
implementation that does not touch test design or execution.

Use the actual target runtime and the shared TDD/self-review policy;
container use is conditional on the project, not mandatory.

Test observable component behavior through Vue Test Utils; test frontend
domain, application, adapters, stores, router policies, and composables
at their natural boundary. Use an actual router for routing behavior and
real Pinia actions when integration is under test.

Prefer exact scalars/strings. For JSON-compatible objects compare
complete canonical JSON; use a fixture only when the exact canonical
value is strictly over 99 Unicode characters. Do not auto-update
snapshots or inspect source text when behavior can be observed.

After writing tests, obtain the intended behavioral RED signal and perform
executor test self-review. After implementation run targeted tests and the
maintained fast smoke, with relevant format/lint/type checks. Affected full
suites belong only to an explicitly assigned release-preparation Task. Build
and browser checks apply only to explicit acceptance requirements and do not
require or authorize host publication. Save evidence through existing Poise
owners; do not restore ERP self-review registries.

## Core

Open only the row matching the current core API/configuration decision;
this table is an index, not a preload list.

| Topic         | Description                                                     | Reference                                    |
|---------------|-----------------------------------------------------------------|----------------------------------------------|
| Configuration | Vitest and Vite config integration, defineConfig usage          | [core-config](references/core-config.md)     |
| CLI           | Command line interface, commands and options                    | [core-cli](references/core-cli.md)           |
| Test API      | test/it function, modifiers like skip, only, concurrent         | [core-test-api](references/core-test-api.md) |
| Describe API  | describe/suite for grouping tests and nested suites             | [core-describe](references/core-describe.md) |
| Expect API    | Assertions with toBe, toEqual, matchers and asymmetric matchers | [core-expect](references/core-expect.md)     |
| Hooks         | beforeEach, afterEach, beforeAll, afterAll, aroundEach          | [core-hooks](references/core-hooks.md)       |

## Features

Open only the row matching the current feature decision; this table is
an index, not a preload list.

| Topic        | Description                                                    | Reference                                                    |
|--------------|----------------------------------------------------------------|--------------------------------------------------------------|
| Mocking      | Mock functions, modules, timers, dates with vi utilities       | [features-mocking](references/features-mocking.md)           |
| Snapshots    | Snapshot testing with toMatchSnapshot and inline snapshots     | [features-snapshots](references/features-snapshots.md)       |
| Coverage     | Code coverage with V8 or Istanbul providers                    | [features-coverage](references/features-coverage.md)         |
| Test Context | Test fixtures, context.expect, test.extend for custom fixtures | [features-context](references/features-context.md)           |
| Concurrency  | Concurrent tests, parallel execution, sharding                 | [features-concurrency](references/features-concurrency.md)   |
| Filtering    | Filter tests by name, file patterns, tags                      | [features-filtering](references/features-filtering.md)       |
| Test Tags    | Label tests with tags to filter runs and apply shared options  | [features-test-tags](references/features-test-tags.md)       |
| Reporters    | Built-in reporters, default selection, CI/output config        | [features-reporters](references/features-reporters.md)       |
| Benchmarking | Write benchmarks with the bench fixture (Tinybench)            | [features-benchmarking](references/features-benchmarking.md) |

## Advanced

Open only the row matching the current advanced decision; this table is
an index, not a preload list.

| Topic        | Description                                             | Reference                                                    |
|--------------|---------------------------------------------------------|--------------------------------------------------------------|
| Vi Utilities | vi helper: mock, spyOn, fake timers, hoisted, waitFor   | [advanced-vi](references/advanced-vi.md)                     |
| Environments | Test environments: node, jsdom, happy-dom, custom       | [advanced-environments](references/advanced-environments.md) |
| Type Testing | Type-level testing with expectTypeOf and assertType     | [advanced-type-testing](references/advanced-type-testing.md) |
| Projects     | Multi-project workspaces, different configs per project | [advanced-projects](references/advanced-projects.md)         |


## Inputs

Current Poise Task/stage/scope, accepted observable contract and independent oracle, actual Vue/TypeScript/Vitest/Vite versions, session root, test topology and selected runtime/commands. Read project docs/manifests as facts while retaining Poise configuration inside Poise. Use [poise-workflow](../poise/SKILL.md).

## Procedure

Select only the API/configuration reference required by the actual version and operation. Write exact independent expectations for the natural behavior boundary; use real routing and real store actions when their integration is the subject. JSON-compatible expectations are complete canonical JSON: values **strictly greater than 99 Unicode characters**, including control characters in their canonical representation, belong in independent UTF-8 JSON files; 99 is not greater than 99. Do not derive the expected value from the production function, convert meaningful mismatches into partial/wildcard assertions, or impose a serializer package. Keep fixtures minimal and explicit.

Preserve the intended RED, exact commands/exit results and the bounded GREEN evidence. A setup/import/environment failure is not the intended RED. `update`, snapshot regeneration, `.only`, `allowOnly`, skip/retry/coverage examples document APIs; they never authorize bypassing obligations, rewriting expectations to match a bug, lowering coverage or passing incomplete checks. A separately authorized expectation change still needs an independent specification and reviewed diff. Ordinary automation rejects exclusive tests and does not update snapshots. Use targeted selectors and maintained smoke rather than broad fallback discovery. Read [reference validation](REFERENCE-VALIDATION.md) before adapting version-sensitive snippets; a sample marked v5 is not certified for v4.

## Output

Requirement-to-test/oracle mapping, exact runtime/selection, RED/GREEN and focused validation results, remaining target build/browser limits and executor self-review through existing Poise evidence/artifact/result APIs. Public handoff is not independent review, acceptance, publication or automatic area/stage skill routing.
