---
name: system-tests
description: Design and verify explicitly assigned system, acceptance or release journeys across real application boundaries, with independent setup and source/runtime evidence.
---

# System and acceptance verification

Use for an acceptance/release assignment or an explicit end-to-end journey,
not every unit/component/integration change or browser-visible feature.
Follow the [system verification contract](../../../docs/governance/verification-plan-checklist.md#системные-и-приёмочные-проверки)
and the selected project's test/runtime profile.

Define the user journey, exact source/build/environment, external entry points,
direct test-owned data setup, expected visible and persisted states, relevant
network/queue/browser boundaries, and cleanup. Keep expected values independent
of production wrappers, classes and serializers.

Select the actual maintained scenario/package through its owner. A diagnostic
subset does not replace a required complete scenario. Ordinary development still
uses targeted checks and maintained smoke; full release validation needs its
own explicit assignment and [release gate](../release-gate/SKILL.md).

For a browser journey choose engine, viewport and observation method from the
accepted requirement and project configuration. Load a browser test skill only
when an automated browser test is written, changed or run. Screenshots do not
replace required assertions; automation does not silently replace specifically
required human/browser observation.

When relevant, cover navigation/direct entry/refresh, shared state, validation
and recovery, save payloads and persisted results, dialogs, focus/keyboard and
responsive states. Prove that the host serves the tested build when assets matter.
Store logs under the configured Task/Sprint evidence owner and report unproven
boundaries honestly. Product release and Task acceptance remain distinct;
no ERP-specific CJM ID, suite directory or browser is implied.
