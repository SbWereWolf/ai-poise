# Focused, smoke and release scope

Read only while selecting verification scope or interpreting cache/reuse.
The [runner](../../../../docs/workflows/runner.md#именованные-проверки-проекта),
[verification matrix](../../../../docs/governance/verification-plan-checklist.md)
and [tooling checks](../../../../docs/workflows/tooling-checks.md) own the contract.

Use the smallest falsifying RED/GREEN for changed behavior. Complete every
required named package for its declared responsibility; a diagnostic subset
does not replace it. Add the maintained fast smoke required by the selected
project. Ordinary work does not fall back to a broad/full suite.

Full/fresh release coverage belongs to an explicit release assignment. Do not
copy ERP fast/full IDs or require frontend/build/browser checks for an unrelated
target. Resolve exact commands and environments through the configured owner.

A reusable result needs the owner's actual compatible cache/provenance decision;
a recent-looking log or unchanged Git label is insufficient. Preserve complete
machine outcomes, distinguish diagnostics from required gates, and never call
missing/unconfirmed validation PASS.
