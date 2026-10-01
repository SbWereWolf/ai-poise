# T027 independent maintenance fixtures

These are test-owned inputs and literal oracles, not serialized production output.
`@PYTHON@`, `@PROBE@`, `@SETTINGS@`, `@STATE@`, `@AUDIT@` are exact test arrangement
slots replaced before an input is written. They are not Bootstrap interpolation.
The command probe is an external process and never imports production symbols.
Its audit is an explicitly separate instrumentation channel; business inputs and
sentinels must remain unchanged for readonly cases. It records only three synthetic
environment names, never the host environment. All token strings are synthetic.

`check-core.json` is the complete literal expected core report for the ready check.
The test first validates each actual duration as a finite nonnegative number, then
normalizes only `duration_seconds` to zero. No production result updates fixtures.
`cases.json` preserves the independent apply/recheck truth table and documentation
requirements. The permanent `maintenance.check` is the exact RED selector and part
of GREEN. Infrastructure exceptions remain errors, not the expected RED assertion.

Profile compatibility migrates T026's intentionally obsolete unavailable results
to real empty-catalogue core results. All twenty group identities and original
negative access/JSON/path/budget/sentinel guards remain. Repairs is a mapping as
required by the existing core. Undeclared values now fail; values-digest input
explicitly declares its parameter. A missing `secret` flag is not an implicit
secret in the core: the digest is suppressed conservatively, but a valid public
parameter value remains visible. Invalid declarations and declared secrets retain
strict nondisclosure checks. No old registry receipt or accepted commit is edited.
