# Результаты F1: routing и repository tools

Обновлено: 2026-09-15. Baseline `729e941`, ветка `migration/f1-routing-tools`.
Отклонённая старая F3 не использована. Task DB не изменена; кодовый результат не
подменяет independent review, acceptance, integration или lifecycle `completed`.

| Task | Содержание и границы |
|---|---|
| C026.2 | [Конкретная папка и proof индекса](../../workflows/code-navigation.md#конкретная-папка-и-подтверждение-индекса): один probe owner, без нового indexer/root policy. |
| C002 | [Reuse](C002.md): повторная реализация не требовалась. |
| C025 | [Среда runner](../../workflows/runner.md#среда-исполнения-и-точные-команды-ai-poise): прежние runtime owners плюс ранняя диагностика unsupported host. |
| C003 | [Selection metadata](../../configuration/development-routing.md#декларативные-условия-выбора-skills): явные facts/conditions; 35 skills остаются в каталоге. |
| C027.4 | [Markdown fallback](../../workflows/markdown-formatting.md#явное-форматирование-при-недоступной-ide): explicit-file check/fix, EOL, fences, таблицы и idempotence. |
| C004 | [Один router](C004.md): прямой вызов, bootstrap/refresh, правила/skills/checks и derived snapshot, без собственного lifecycle. |
| C043 | [IDE audit](C043.md): прежний skill + доказанные документационные исправления. |

Gmail-отправка первой исходной копии отклонена системным safety filter. Изменять
упаковку для обхода блокировки нельзя. Каждый следующий checkpoint сохранён локально
и проверен восстановлением, но **не объявлен доставленным в Gmail**. Локальный `verify`
не выдаётся за `seal-delivery` или remote readback.

C004: три дополнительных batch-теста падают одинаково на коде до C004 и после него;
сравнительные логи сохранены. Они не исправлялись и не засчитаны в успешные проверки.
Full suite не запускался. Отсутствие живой IDE/AST/host установки не заменено fixture PASS.

При подключении router команды/операционные метаданные относятся к работающему Poise;
конкретная копия используется только как subject чтения/тестирования. Не вводятся
альтернативные корни, generic repo-schema, автоматическая настройка чужих репозиториев,
дублирующий runner или второй IDE skill. Runtime placement worktree не изменяется.
