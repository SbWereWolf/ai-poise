---
name: safe-commit
description: Preserve unrelated changes when making explicitly authorized direct commits or recovering a Task-owned checkpoint, handoff or integration commit.
---

# Safe commit

Use the [commit owner and authority contract](../../../docs/workflows/commit-messages.md#низкоуровневый-коммит)
and [message format](../../../docs/workflows/commit-messages.md#продуктовый-смысл-и-формат-5070).
This skill does not replace Task finalization, independent acceptance or integration.

## Task-owned delivery

An assigned Task authorizes its necessary stage, checkpoint, handoff and integration
commits within its contract. Use the existing public owner and `commit_message`;
do not request another permission or create a second commit after successful `verify`.
If finalization was interrupted, inspect the actual outcome and use
[harness recovery](../harness-recovery/SKILL.md); never invent a receipt or edit managed state.

## Explicit direct commits and recovery

1. Establish the authorized repository, branch, delivery route and owned paths.
   A non-Task commit requires the user's explicit authorization.
2. Inspect both working and staged changes. Preserve unrelated work and stage only
   owned paths; unclear ownership requires a user decision before alteration.
3. Verify the staged diff and the checks appropriate to this change. When ordinary
   Git is the authorized owner, commit those paths with the required 50/70 message.
   Use the configured Task operation when that operation owns delivery.
4. Read back the resulting commit and its changed paths. Keep commit, verification,
   acceptance and integration as separate facts. Never push.

Read-only review and a final response do not themselves require a commit. A result
containing only workflow materials follows its existing artifact/delivery contract.
Do not copy another repository's commit wrapper, authorship flags or forbidden-path list.
