# Canonical instruction ownership

Repository-wide invariants and executable entry points belong in the root `AGENTS.md`. Local surface rules belong at their actual owner. A skill contains a trigger, inputs, procedure and output; supporting references hold only decision-specific detail. Human-facing product documentation remains canonical for architecture and operator workflows, not a duplicated agent-only policy.

Poise owns Task/Sprint lifecycle, evidence, content, artifacts, runtime and project configuration through their existing APIs. Skill files guide use; they do not own a second state machine. Target-worktree documentation/configuration is read-only factual input for Poise routing settings, never a storage location for those settings.

Load the actual Task/stage packet first, then relevant skills/references on concrete scope, behavior or risk. Resolve contradictory rules by their authority and recorded decisions; do not silently prefer a convenient duplicate. If automatic routing is missing, expose that integration dependency rather than treating manual directory discovery as runtime implementation.

When a rule changes, update its canonical owner and affected links/examples in the same authorized work. Verify names, triggers, paths and the distinction between planned and implemented behavior. Do not migrate legacy task/report registries, numeric role prefixes, mandatory four-role ceremonies or a copied document-read lifecycle.

Reviewers independently inspect substantive correctness; executor self-review does not acquire reviewer authority. The repository's [development rules](../../../../docs/governance/development-rules.md#общие-правила-агентов) and [work API](../../../../docs/workflows/batch-work.md#ответственность) remain the live contract.
