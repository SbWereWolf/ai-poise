# T026 fixture provenance

These inputs and expected values are authored independently of production code.
`selected-result.json` pins the externally visible single-profile result and an
independent SHA-256 of the canonical `project/catalog/repairs/values_files`
snapshot described in the accepted plan. The same permanent selection assertion
is used for RED and GREEN. No normal test run generates or updates expectations.

All fixtures are inert inputs copied into temporary directories. The side-effect
catalogue contains commands that must never execute in this T026 selection-only
slice; complete maintenance validation and execution belong to T027.
