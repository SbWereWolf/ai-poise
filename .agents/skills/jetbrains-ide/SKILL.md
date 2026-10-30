---
name: jetbrains-ide
description: Use an available project-bound JetBrains MCP for semantic navigation, changed-file diagnostics and scoped refactoring without replacing runtime tests or repository workflow owners.
---

# JetBrains semantic tools

Follow the [canonical IDE policy](../../../docs/governance/jetbrains-mcp-policy.md#правило-выбора-инструмента)
and its [capability reuse rule](../../../docs/governance/jetbrains-mcp-policy.md#повторное-использование-проверки-capability).
Load this skill for an actual semantic lookup, refactoring or IDE diagnostic need,
not documentation-only work.

At the first need, inspect exposed tools and perform one available read-only
status or semantic call for the exact project/worktree. A passed projectPath
argument alone does not prove binding. Distinguish available, unavailable,
unhealthy and unsupported-project observations; reuse an unchanged observation
instead of repeatedly probing an absent capability.

For a supported ordinary symbol lookup, prefer the project-bound IDE: use the
exposed search operation, select a candidate, then obtain signature, ownership,
hierarchy or usages. PhpStorm/PyCharm/WebStorm applicability follows the actual
project and language support, not a mandatory endpoint order or file extension.
Use literal search for routes, serialized keys, documentation and other text.

Repository-wide graph, reproducible batch and structural queries may use the
configured AST owner directly. For semantic fallback state the actual missing
capability; do not label an AST result as an IDE inspection or refactoring.
Keep candidate results bounded and read source only for selected candidates.
Use the selected checkout/index configuration; do not copy ERP's wrapper or paths.

Before code handoff, inspect changed supported owned source files with the
available file-problems operation. Repeat only for repaired files, then run the
project's real focused checks. IDE diagnostics supplement behavior evidence.
Before a semantic rename distinguish code usages from serialized/public names;
afterward inspect the full diff and affected literal contracts.

Use one formatter selected by the actual project policy. Do not chain another
formatter after successful IDE formatting. Do not use IDE build/run, debugger,
generic terminal/file mutation, Git or database tools in place of Poise's owners;
the [existing boundaries](../../../docs/governance/jetbrains-mcp-policy.md#границы-и-исключения)
remain in force. Record actual binding, operations, limitations and applicable
checks in current evidence, without a dedicated capability-report artifact.
