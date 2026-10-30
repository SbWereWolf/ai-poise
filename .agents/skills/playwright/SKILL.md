---
name: playwright
description: Write, change, diagnose or run automated Playwright browser tests through the selected project's runtime, with observable UI assertions and source/build evidence.
---

# Automated browser tests

Load for actual automated browser test work, not merely browser-visible code.
Acceptance strategy belongs to [system-tests](../system-tests/SKILL.md); apply
both when that accepted journey uses an automated browser test. Follow the
[browser verification contract](../../../docs/governance/verification-plan-checklist.md#автоматизированная-браузерная-проверка).

Resolve the installed Playwright version, test topology, launcher, browser
runtime, application instance and output paths from the selected project.
Use its maintained suite and reusable setup/actions, not ERP's directories,
environment wrappers or a fresh dependency/browser installation.

Use semantic user-facing locators or stable test IDs and independent literal
expectations. Observe the real UI/network/persisted result; production code or
its wrapper must not generate the expected answer. Prefer supported event/state
waits over sleeps. Do not couple to generated CSS unless styling is the contract.

Choose relevant route/direct-entry/back/forward/refresh, shared-state, validation,
save, dialog, focus/keyboard, loading/error and responsive scenarios. The accepted
requirement and explicit project configuration choose browser/viewport; a Firefox
requirement needs actual Firefox evidence, not a substituted engine.

Run the configured focused scenario and applicable maintained guard. When the
journey depends on frontend assets, verify the host serves the tested build.
Keep JUnit/logs/screenshots/traces in the declared Task/Sprint or project-owned
evidence location; do not invent root or suite-local var directories.
Screenshots/traces support assertions and remain subject to the file/handoff policy.
Inspect tests before implementation, and code/results before handoff; do not
auto-update snapshots or claim a browser/host operation that was not executed.
