# AI poise test rules

Apply these rules when creating, reviewing, or registering verification methods that execute tests under this directory.

- Give every check one explicit earliest stage at which it is expected to be GREEN. Do not register it as a required GREEN check for an earlier stage.
- Accept RED only when the declared failure predicate matches the exact intended failing tests. Any additional failure makes the RED evidence invalid and must be resolved or split before implementation starts.
- Split behaviour, documentation, and final regression checks when their required changes belong to different workflow stages.
- Keep tests outside the current change surface in a separate GREEN guard so an expected RED cannot hide an unrelated regression.
- Record and apply the verification-plan matrix from [the canonical checklist](../docs/governance/verification-plan-checklist.md) before method registration and again before leaving the test stage.
- Use the declared responsibility surface and observed failures. Do not attempt recursive inference of every file a test could read.
