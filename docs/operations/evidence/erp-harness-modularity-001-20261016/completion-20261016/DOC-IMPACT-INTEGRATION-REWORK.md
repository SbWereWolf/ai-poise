# Documentation impact of integration rework

Status: co-delivered.
Checkpoint commit: 0719cdf67fe789f54798f82775aae6597c316ade.
Tree: 4f2f1a03175f6cdda5b31f826932a6a8358f1e50.

The inherited target contract in
docs/tools/system-dev-tooling/registered-check-evidence.md already treats
registered-runner record/log/evidence checksums as diagnostic information,
not admission gates. Three older test consumers still expected mandatory
hash fields or the retired tracked .codex/hooks.json path.

Canonical co-delivery: docs/agents/check-cache.md now describes available
source records, confirmed success and current declared input fingerprints,
and links to the existing evidence contract instead of requiring record
checksum equality. This does not change native Poise termination, identity,
history or audited recovery preimage guards.

The original five-file documentation method remains unchanged. The added
canonical path has its own actual registered narrow proof:
chk_20261016T081329Z_188a46dcdf, default documentation profile,
markdown_files1/scoped_markdown_targets1/findings[]/exit0/verified_success=true.
The formatter ran on that one path before the successful check. The prior
formatting failure3d040da8ea is retained as a real failed result.
