# Integration self-review

Candidate index tree: c2fcc025aa20b2be70432fd2ff3d45ea855c2329.
Accepted source: a8ae8509d8c1204805e892bf6b2f24bb37e7b91a.
Observed target: 16c5b906615e57a5b5f89b495c42b79f51eab87e.

- All nine conflicts were resolved in the existing Task worktree. Three
  adjacent canonical-owner/test-consumer files also required migration.
  The primary checkout and its foreign tracked/untracked changes were not edited.
- Profile declarations, validation and serialization remain in
  command_profile_lib. Strict known configuration validation is retained.
  New target public argument grammar is preserved through its shared owner;
  its optional selector semantics do not change profile configuration validation.
- CLI and MCP delegate execution to RegisteredCheckService with explicit
  CheckConfiguration. Execution subject, configuration root, data owner and
  physical checker source remain separate. No compatibility shim or new package.
- One shared profile_loading_context preserves original exception types and
  applies the existing contextual recovery diagnostics to all loader callers.
- Both required fast suites remain present. Literal inventory135/103 accounts
  for the target133 declarations plus two actual source variants. The previously
  omitted runner lock-release test is now executed with declared bookkeeping
  identity and retains its exact postcondition. Fourteen runner tests passed.
- Focused HM24 and MCP contracts passed. Final registered fast passed117.360s,
  including115 admission/lock tests80.730s. The former92.241s direct test run
  exceeded60s; subsequent execution used registered fast, never direct replay.
- Failed checks and two empirical timeouts were not reused as success. Diagnostic
  retries reference real terminal evidence; no timeout profile was modified.
- git diff --check passed; no unresolved index stages or unstaged changes remain.
  The child artifact audit passed95 reservations. Frozen source and terminal
  check files were copied outside child/Task and their hashes were verified.
- Independent inspection of this integration candidate remains required;
  historical approval of a8 alone does not approve the new resolution hunks.
- Retention error is explicit: full original reviewer stderr/response were
  retired before copying. The actual public terminal snapshot, native report
  content and acceptance receipt survive; no original log was reconstructed.
