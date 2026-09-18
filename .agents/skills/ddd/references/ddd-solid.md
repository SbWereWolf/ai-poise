# DDD and SOLID working standard

Use DDD to keep business meaning and responsibility explicit. Do not use
it as ceremony. A small local operation may stay local when it does not
hide a reusable rule, mix unrelated responsibilities, or cross an
ownership boundary. Split code when a rule, invariant, transaction,
integration, or repeated concept would otherwise be scattered through
controllers, models, adapters, framework callbacks, or task-local
helpers.

For every behavior or executable-configuration change, record a compact
DDD placement decision:

- owning business capability or bounded context;
- ubiquitous-language terms from the technical specification;
- rule owner: entity, value object, aggregate, domain service,
  application service, policy, port, adapter, or presenter;
- aggregate or transaction boundary when the change writes state;
- integration boundary crossed: API, event, queue, CLI, filesystem,
  database, browser, or external service;
- existing-behavior search scope, behavior terms, contracts, tests,
  usages, and candidate production symbols inspected;
- reuse decision: reused, extracted, or kept separate; name the
  canonical owner and consumer composition/injection path, or explain
  the semantic, ownership, or bounded-context difference;
- rejected destinations and why the rule does not belong there, such as
  transport controller, persistence callback, generic service,
  infrastructure adapter, or one-off helper.

### Discovery sequence

1. Study the domain workflow, roles, invariants, failure cases, and
   current technical specification before naming classes.
2. Search the owning context and adjacent owned layers by behavior,
   domain terms, contracts, tests, call sites, and usages; do not limit
   discovery to matching symbol names.
3. Compare candidates by semantics, responsibility, owner, inputs,
   outputs, failures, and side effects. Reuse the canonical owner or
   extract one and replace every duplicate within the accepted scope.
4. Keep candidates separate only when they implement different business
   decisions or belong to different owners or bounded contexts, and
   record that reason.
5. Form or reuse the ubiquitous language in class, method, state, error,
   test, and fixture names.
6. Identify the bounded context that owns the decision and data.
7. Model the rule with the smallest useful owned shape: entity, value
   object, aggregate, domain service, policy, application use case,
   function, or composable. Use a reusable presentation component when
   the shared responsibility is visual composition rather than domain
   behavior. Do not create a class merely to make code look shared.
8. Define aggregate and transaction boundaries for state changes.
9. Design integrations with explicit contracts: API, queue, event, ACL,
   CLI, file, or adapter boundary.
10. Implement around the domain meaning, not around tables, UI widgets,
    request arrays, or framework callbacks. Consumers retain only
    composition/injection and their owned transport or presentation
    glue.

### Selected project placement

Resolve bounded contexts, code locations, dependency direction and framework composition from the selected Poise project's routing/architecture profile. That profile is owned and stored by Poise. It may reference existing target-worktree architecture documents as read-only facts; do not create a target AI poise config or duplicate the profile into mutable runtime state.

Record the actual owner/layer/port and rejected alternatives in current Task content. Use the project's real static/architecture checks where configured, rather than the former ERP application map. Missing mapping is explicit input to the owning configuration work, not an inferred framework requirement.

### Abstraction threshold

Add a new abstraction only when at least one condition is true:

- it protects a domain invariant or transaction boundary;
- it names a business concept from the ubiquitous language;
- it prevents the same rule from being implemented in multiple entry
  points;
- it isolates an external owner or substitutable dependency;
- it makes a failure state, idempotency rule, or side effect testable;
- it removes mixed responsibilities from a class that otherwise handles
  transport, persistence, business decision, and presentation together.

Do not add a repository, service, DTO, interface, aggregate, event, ACL,
or adapter merely to satisfy a pattern. Boilerplate that only moves one
obvious line behind another name is a design regression.

## SOLID review

### Single responsibility

A class should have one cohesive reason to change. Split transport
formatting, business decisions, persistence, and external process
execution when they vary independently.

### Open/closed

Prefer extension through explicit strategies or policies when variation
is already a requirement. Do not pre-build plugin systems for
hypothetical use.

### Liskov substitution

Ports and implementations must preserve result, failure, idempotency,
and side-effect contracts. A fake used by tests must not accept states
rejected by the real adapter.

### Interface segregation

Expose the smallest capability required by a caller. Avoid broad manager
or client interfaces that combine unrelated reads, writes, and
operations.

### Dependency inversion

Domain/application rules depend on ports and value types when the
dependency is external, substitutable, reused, or part of a boundary. Do
not force a port for every local call. Runtime composition wires
concrete adapters at the edge; pure domain code must still stay free of
Laravel, MoonShine, Vue, filesystem, HTTP, process, database, XML/DOM,
and CLI details.

## Readability and maintainability

Prefer:

- intention-revealing names and short cohesive methods;
- explicit state transitions and result types;
- immutable inputs for accepted operations;
- early validation at the owning boundary;
- one canonical transformation for each contract;
- comments that explain why or a non-obvious invariant, not what syntax
  does.

Review complexity, duplication, coupling, and test setup cost after
green. A refactor is justified when it makes the requirement-to-result
path easier to follow or reduces the chance of inconsistent changes.
