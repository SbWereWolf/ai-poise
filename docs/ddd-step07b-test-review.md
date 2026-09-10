# Test review — DDD-07B

Updated: 2026-09-07T00:39:00+05:00.

The initial test commit precedes the Transfer API. Its RED shows the missing transfer configuration/API, not a failure in the application being developed. Tests use real SQLite, worktrees, Git bundles and ZIP files. The primary path includes a second destination task with its own submission so that numeric IDs cannot accidentally be assumed global.

Covered contracts: WIP and verified restore, source directory removal before reading preserved output, exact section text, relocated artifact references and stable IDs, batch of standalone tasks, complete sprint membership/result dependency, source session exclusion, event de-duplication, replay without resetting progress, incompatible policy and existing owner rejection, filesystem containment and integrity, SQL rollback plus retry. No live ChatGPT, IDE or Gmail connection is simulated as accepted.

One self-review regression was observed before its fix: a newly hashed copy of an artifact could be packaged even though it no longer matched its already registered digest. The test now requires that the earlier evidence identity be respected, not merely that the archive match the changed file.

Tests deliberately do not demand automatic merging of an existing owner, partial-sprint synchronization or live external transport. These remain explicit limits of this implementation slice.
