# Operational observation

Use the supplied native entry and explicitly selected project. A pure
observation is
validated `show` with `messages=[]`; it requires initialized
current-schema stores. Standalone Requirements `operation=query` uses the
same observation boundary.
Prepare an empty project through explicit taskless `bootstrap`, not by
relying on a query.

- Preserve existing caller inventory, claims, cursors and primary
  message bindings.
- Keep first generated-caller admission and explicit
  message/transcript ingestion with
  their write owners. Do not infer global absence of effects from
  `status=read_only`.
- Use the owning configured read transaction before entering a query
  body; preserve
  one body execution, explicit `limits.sqlite_read_seconds`, full error/output
  and optional telemetry. Keep writer wait independent; publish the required
  common setting through the project owner before activating the consumer.
  Malformed packets with a valid config retain a bounded/full rejection through
  the existing result owner without caller/store establishment.
- Never replace native identity, fabricate an event,
  initialize/migrate a store from
  observation, restore a shared installation fence or retry an unknown
  operation.

Full operator contract and failure semantics:
[runtime observations](../../docs/configuration/runtime-services.md#наблюдение-и-пишущие-намерения).
Declarative
inputs: [batch API](../../docs/workflows/batch-work.md#ответственность).
