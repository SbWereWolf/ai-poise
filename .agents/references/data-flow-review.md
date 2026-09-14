# End-to-end data-flow review

## Reconstruction method

Reconstruct the path in both directions.

Forward:

```text
input -> validation -> transport adapter -> application use case
      -> domain decision -> persistence/external adapter
      -> presenter/serializer -> observable result
```

Backward:

```text
observable field/error/artifact
  -> producer
  -> source state and transformation
  -> original input and requirement
```

The backward pass catches fields that appear without a trustworthy
source and transformations that are not visible from the entry point.

## Questions at every hop

- What exact type and invariant enter this hop?
- Which layer owns validation here?
- Is the value copied, normalized, enriched, or interpreted?
- Can null, absence, stale version, duplicate delivery, or retry reach
  it?
- Which error type or state leaves the hop?
- Is the transaction, idempotency, and side-effect boundary visible?
- Is logging/audit/correlation evidence sufficient to reconstruct
  failure?
- Which test observes this hop without over-mocking the behavior away?

## Review output

For non-trivial flows, produce a compact map with concrete symbols:

```text
POST /v1/...
  -> Controller::__invoke
  -> LaunchOperation::handle
  -> OperationPolicy::authorize
  -> OperationStore::create
  -> CalculationClient::launch
  -> OperationView::fromState
```

Annotate ownership changes and irreversible side effects. Flag:

- primitive arrays crossing several layers;
- the same normalization repeated in different adapters;
- hidden framework callbacks that own business rules;
- entities exposed directly as transport models;
- writes before validation/authorization is complete;
- success responses before durable state or artifacts are consistent;
- swallowed errors or fallback values that erase the original cause.

## Readability standard

A maintainer should be able to discover the flow from names and explicit
calls without mentally executing framework magic. Prefer application
services, value objects, DTOs, and narrow ports where they reveal
ownership. Do not add abstractions that only rename one line or obscure
a local, obvious operation.
