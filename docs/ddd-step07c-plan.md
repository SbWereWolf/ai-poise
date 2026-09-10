# DDD-07C — Hook transport and capability probes

Started: 2026-09-07T01:17:28+05:00.

## Goal / DoD
One declarative install packet generates a managed Codex hooks.json configuration while preserving unrelated hooks. Native SessionStart/UserPromptSubmit/Stop/SessionEnd events bind an isolated actor, count actual prompt events without storing prompt text, and return a session-scoped work entry point. They do not accept tasks or run task lifecycle transitions on their own. Exact read-only capability probes return actual observations; a manifest is not a probe. Probe failure remains distinguishable from a Harness failure. Work calls reuse the current WorkTools API.

## Scope and limits
Linux, explicit project, no Codex trust bypass, no editing ~/.codex unless explicitly selected. This slice targets root-session command hooks; no implicit parent/subagent attribution. Reuse the existing JSONL adapter as a separate explicit source, never mix both message streams. Exact command probes and MCP stdio initialize/tools/list/optional configured read-only smoke call; no automatic server discovery, credential setup, SSE/HTTP fallback or remote installation. No live IDE or user Codex is present: local protocol tests are not live compatibility certification.

## Ownership
HookDefinition owns configured event mapping. CapabilitySpec owns planned observations. HookCommands/CapabilityChecks use ports; filesystem/SQLite/Git/subprocess belong to infrastructure. Task/Sprint lifecycle stays behind WorkTools. Hook state is a separate operational registry, not a second Task store.

## TDD sequence
Tests and explicit fixtures → RED → inspect tests → implement domains/ports/adapters → full GREEN → inspect code/fixes → executable demo/docs → full and changes archives. Existing DB and task schema need not change for an additive operational registry; no migrations.

## Ordinary routes
Install new → reinstall same → edit preserving foreign hooks; stale input rejected. SessionStart → prompt → task bootstrap with event binding → repeated prompt/work without double count → verify/report → Stop (no auto acceptance). Probe a real executable; unreachable/misbound service stays unavailable. MCP handshake → bounded tool inventory → optional smoke and project predicate. Request all probes in one batch and embed checks in work boundaries, not one model call per capability.
