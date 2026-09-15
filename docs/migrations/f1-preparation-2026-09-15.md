# Подготовка F1: routing и repository tools

Обновлено: **2026-09-15**. Этот документ фиксирует подготовку к `SPRINT-ERP-MIGRATION-F1-ROUTING-TOOLS`; выполнение F1 здесь **не начинается**. Источник состава — sealed Task DB из итогового checkpoint после F2/C027 и dependency-closure audit.

## Baseline и границы

- Git baseline до подготовки: `b03b6f4667c7deaf4bcb7a95996d5e28b2d8f9ea`.
- Старая отклонённая F3 не входит в ancestry baseline.
- Task DB в подготовке не изменяется; draft/lifecycle остаются плановыми данными.
- C027.1/C027.2/C027.3 уже реализованы standalone и **не возвращаются** в F1.
- C026.1 и C051 остаются retired; их нельзя выполнять.
- Работа выполняется по обычной семантике путей Poise. Конкретной операции можно передать конкретный filesystem path/cwd; это не создаёт второго root.

## Замкнутый граф F1

```text
C002 (conditional duplicate) -> C003 (conditional duplicate) -> C004 (conditional duplicate) -> C043 (conditional duplicate)
C026.2 ---------------------------------------------------------------> C043
C002 (conditional duplicate) -> C025 -> C027.4
```

Состав: 7 Tasks, 6 hard edges, `dangling=0`, `isolated=0`, `cycles=0`. Число Tasks не является целью; это фактический dependency-closed граф.

## Решения preflight

| Task | Решение перед исполнением | Почему |
|---|---|---|
| `ERP-MIG-F1-11-C002` | **reuse/no-change** | F2 уже реализовала C002 (`d301978`): scoped development rules, точный раздел path semantics и AGENTS-ссылки. Повторная реализация запрещена. |
| `ERP-MIG-F1-04-C026-2` | **implement narrow gap** | IDE-first policy есть, но в текущем source нет владельца операции, который принимает конкретный checkout path и диагностирует identity/freshness AST index. Не создавать второй IDE owner и не встраивать собственный indexer. |
| `ERP-MIG-F1-08-C025` | **audit first; likely reuse + narrow gap-fill** | Runner/capability owners после F2 уже дают exact argv/cwd, process ownership, timeout и availability observations. Сначала доказать конкретный AI-poise gap; generic runtime policy для чужих проектов запрещена. |
| `ERP-MIG-F1-02-C027-4` | **implement** | Markdown formatter fallback в текущем репозитории отсутствует. Нужен только explicit-file check/fix fallback AI-poise, IDE остаётся preferred path. |
| `ERP-MIG-F1-12-C003` | **implement** | Skill Catalog существует, но selection-condition metadata, потребляемой одним router, нет. ERP profiles не переносить. |
| `ERP-MIG-F1-13-C004` | **implement** | Единого routing owner для `rules + shipped skills + registered checks` из explicit task/change facts в текущей линии нет. |
| `ERP-MIG-F1-14-C043` | **reuse after prerequisites, then gap-fill only if proven** | IDE policy и `poise-development` skill уже есть. Решение принимается после C026.2+C004; второй skill/router запрещён. |

Machine-readable версия: [F1 preparation JSON](f1-preparation-2026-09-15.json).

## Порядок одного прогона

Использовать сохранённый детерминированный topological order:

1. `ERP-MIG-F1-04-C026-2`;
2. `ERP-MIG-F1-11-C002` — только reuse/no-change verification;
3. `ERP-MIG-F1-08-C025`;
4. `ERP-MIG-F1-12-C003`;
5. `ERP-MIG-F1-02-C027-4`;
6. `ERP-MIG-F1-13-C004`;
7. `ERP-MIG-F1-14-C043`.

Порядок не означает искусственной зависимости между параллельными ветками: он лишь даёт воспроизводимый последовательный прогон. Hard edges остаются только теми, что записаны в Sprint.

## Проверки и checkpoint для исполнения

После **каждой** Task:

1. выполнить только focused tests её owner + затронутых соседних contracts;
2. выполнить bounded `tests/smoke.sh`; full suite не запускать;
3. проверить `git diff --check` и отсутствие незаявленных path/root policy изменений;
4. создать self-contained checkpoint через [контрольную точку и восстановление](../workflows/checkpoint-recovery.md#контрольная-точка-и-восстановление);
5. отправить binary archive + connector-readable recovery transport в Gmail и сделать readback/restore;
6. для conditional duplicate сохранить provenance/reuse decision даже при нулевом code diff.

## Что подготовка намеренно не делает

Она не публикует Sprint, не меняет `newborn`/`draft` lifecycle, не помечает Tasks `completed`, не начинает F3/F4, не реализует отдельную продуктовую задачу размещения worktree и не переносит код из отклонённой F3.
