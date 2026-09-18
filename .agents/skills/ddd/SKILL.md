---
name: ddd
description: Place behavior at its real domain owner using reuse/extract/separate decisions and forward/reverse data-flow analysis.
version: 1.0.0
---

# Domain-driven ownership

## Inputs

The observable behavior and requirement, business capability/bounded context, current Task/stage/scope, changed paths and the selected Poise project's routing/architecture profile. Read referenced target-repository architecture documents from the session-selected worktree as source facts. The profile stays inside Poise; do not duplicate it in mutable runtime state or store an AI poise configuration in the target repository. Use [poise-workflow](../poise/SKILL.md) for current Task/content/evidence operations.

## Procedure

1. Identify the ubiquitous-language terms, business owner, layer, rule owner, aggregate/use case, transaction boundary, ports/adapters and every crossed boundary before naming new classes. Explain why the rule belongs there rather than in transport, presentation, persistence callbacks, a generic service or a task-local helper.
2. Search the owning context and adjacent layers by behavior, domain terms, contracts, tests, call sites and usages—not symbol names alone. Compare actual semantics, inputs/outputs, failures and side effects. Choose explicitly: **reuse** the canonical owner; **extract** one canonical owner and replace every in-scope duplicate; or **keep separate** because semantics, responsibility or bounded context differ. Record candidates and reasons.
3. Implement the smallest coherent owned unit: value, policy, entity, use case, function, composable or component as appropriate. Consumers compose/inject it. Do not force a common class or merge superficially similar code representing different business decisions. The [DDD/SOLID reference](references/ddd-solid.md) gives the abstraction threshold.
4. Keep transport/framework glue thin and dependencies inward. Backend domain is independent of application/infrastructure/delivery details. For frontend use the unchanged layer responsibilities in [frontend DDD](../../references/frontend-ddd.md): domain/interaction rules do not depend on concrete components, state libraries, routers or adapters. Server-owned authority remains server-owned.
5. Let the use case owning the business operation start transactions and irreversible effects. Move a rule to a shared package only for a real cross-runtime need, stable public contract or lifetime independent of the current UI/API shape—not convenience.
6. Reconstruct success and significant failure paths **forward and backward** using [data-flow review](../../references/data-flow-review.md). Name concrete producers, transformations, consumers, messages/tables/artifacts, owning boundaries and test observations. Explain each output's original input/state and requirement; inspect stale/missing/duplicate values, retries and durable completion.
7. Link new/moved symbols, layer/owner/dependencies, requirements and tests through current Poise Task/content/trace/evidence owners. Do not migrate ERP TRACEABILITY.md or create a replacement registry. Run actual configured architecture/static checks when relevant, then targeted checks plus maintained smoke—not full regression in ordinary work.

## Output

A compact placement and reuse/extract/separate decision, concrete forward/reverse flow, changed symbols and existing-owner requirement/evidence links, actual focused check results and unresolved architecture/routing inputs. A missing Poise-owned profile is a recorded integration dependency, not permission to guess ERP locations. Save and hand off through current Poise; independent review/acceptance remains separate.
