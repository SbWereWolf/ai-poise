# Bounded command output without lost truth

The current runner owns process execution, observation, terminal status and durable raw logs. Its configuration owns budgets, runtime, parser selection and recovery. This reference does not introduce a runner, hook, registry or queue.

Before launch, validate explicit inputs, working directory and scope through the owner. During execution, do not start a competing observer or repeatedly ask whether the same command is still active. Only the supported cancellation interface controls an authorized cancellation.

At termination preserve the real exit code/signal/cancellation and complete stdout/stderr in the configured runtime/artifact owner. Summaries contain operation/command identity, source/runtime identity, terminal state, useful bounded failure excerpts, truncation metadata and durable full-log references. Truncated output is never labeled complete. Do not put secrets in logs or scrub correctness-critical diagnostics into apparent success; redact with an explicit marker and keep access appropriately restricted.

A parser enriches raw truth. Parse failure, missing fields, unexpected schema or an empty success-looking message cannot replace a nonzero command status. Conversely, zero exit is not enough when the contract requires a structured failed assertion, unavailable observation or later terminal result. Combine conditions according to the owning command contract, not a generic parser guess. Preserve both underlying failure and presentation failure.

For diagnosis read the saved log through bounded ranges/batches, following the relevant spans to completion. Record which bytes/lines/revision were inspected. Do not claim measured token savings, full-log inspection or deduplication from merely having a budget. Use an existing content reader if available; missing reader capability does not justify a copied ERP session registry.

Verify failure status propagation, Unicode/truncation boundaries, huge output, malformed parser input, exact log links, secret redaction and no false-green result. Optional telemetry loss cannot block the main operation, but losing mandatory business evidence must remain an explicit failure or incomplete result through its current owner.
