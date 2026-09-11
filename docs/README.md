# Документация AI poise

Этот каталог описывает текущее поведение AI poise. Источником требований служит раздел
[«Управление требованиями»](governance/requirements.md), а фактические возможности нужно
сверять со [статусом реализации](architecture/implementation-status.md) и исполняемыми
проверками. Планы, отчёты прежних срезов и результаты разовых пилотов не являются
самостоятельными нормативными источниками.

## Управление и требования

- [Правила разработки](governance/development-rules.md) — обязательный рабочий процесс и
  общие правила агентов.
- [Требования](governance/requirements.md) — нормативные требования и границы продукта.
- [Кандидаты правил](governance/rule-candidates/rule-candidates.md) — ещё не принятые
  предложения; их нельзя применять как действующие правила.
- [Данные спецификации](spec-data/README.md) — машинно-проверяемое представление
  требований. Этот путь пока сохранён из-за программных потребителей.

## Архитектура

- [Границы архитектуры](architecture/boundaries.md) — владельцы доменов и допустимые
  зависимости.
- [Декларативные инструменты](architecture/declarative-tools.md) — граница публичных
  операций.
- [Библиотечный API](architecture/library-api.md) — прикладные интерфейсы и средства
  разработчика.
- [Статус реализации](architecture/implementation-status.md) — реализованные возможности и
  честные ограничения.
- [Архитектурный проект](architecture-design/README.md) — проектные материалы, которые пока
  остаются на прежнем пути из-за тестовых потребителей и не считаются реализованным API.

## Конфигурация и окружение

- [Каталог этапов](configuration/catalogue-stage-map.md)
- [Конфигурация типов целей](configuration/goal-config.md)
- [Каталог процессов](configuration/process-catalogue.md)
- [Настройка проекта](configuration/project-setup.md)
- [Runtime hooks](configuration/runtime-hooks.md)
- [Runtime-сервисы](configuration/runtime-services.md)
- [Источники runtime](configuration/runtime-sources.md)
- [Локальная поставка WSL](configuration/wsl-local-delivery.md)

## Рабочие процессы

- [Действия](workflows/actions.md)
- [Пакетная работа](workflows/batch-work.md)
- [Требования к содержимому](workflows/content-requirements.md)
- [Доказательства](workflows/evidence.md)
- [Рабочий путь](workflows/happy-path.md)
- [Локальная передача](workflows/local-handoff.md)
- [Runner](workflows/runner.md)
- [Sprint](workflows/sprints.md)
- [Перенос сохранённой работы](workflows/transfer.md)

## Эксплуатация

- [Учёт ресурсов](operations/accounting.md)
- [Метрика пользовательских сообщений](operations/user-message-metrics.md)

## Отложенные перемещения

Следующие 29 путей не перемещаются в документационной задаче. На них ссылаются тесты или
другие программные потребители, поэтому перенос должен выполняться атомарно с обновлением
этих потребителей в отдельно разрешённой структурной задаче:

- `docs/architecture-design/MANIFEST.json` → `docs/architecture/design/MANIFEST.json`
- `docs/architecture-design/README.md` → `docs/architecture/design/README.md`
- `docs/architecture-design/architecture_2026-09-06T16-04-53+05-00.md` → `docs/architecture/design/architecture_2026-09-06T16-04-53+05-00.md`
- `docs/architecture-design/data/architecture-model.json` → `docs/architecture/design/data/architecture-model.json`
- `docs/architecture-design/data/stage-library-map.json` → `docs/architecture/design/data/stage-library-map.json`
- `docs/architecture-design/data/storage-catalog.json` → `docs/architecture/design/data/storage-catalog.json`
- `docs/architecture-design/docs/01-domain-model.md` → `docs/architecture/design/docs/01-domain-model.md`
- `docs/architecture-design/docs/02-libraries-and-api.md` → `docs/architecture/design/docs/02-libraries-and-api.md`
- `docs/architecture-design/docs/03-data-storage.md` → `docs/architecture/design/docs/03-data-storage.md`
- `docs/architecture-design/docs/04-runner-and-transactions.md` → `docs/architecture/design/docs/04-runner-and-transactions.md`
- `docs/architecture-design/docs/05-realization-and-checks.md` → `docs/architecture/design/docs/05-realization-and-checks.md`
- `docs/architecture-design/docs/06-ddd-invariant.md` → `docs/architecture/design/docs/06-ddd-invariant.md`
- `docs/architecture-design/evidence/design-check.json` → `docs/architecture/design/evidence/design-check.json`
- `docs/architecture-design/evidence/design-check.md` → `docs/architecture/design/evidence/design-check.md`
- `docs/architecture-design/evidence/input-hashes-after.json` → `docs/architecture/design/evidence/input-hashes-after.json`
- `docs/architecture-design/evidence/input-hashes-before.json` → `docs/architecture/design/evidence/input-hashes-before.json`
- `docs/architecture-design/evidence/prototype-symbols.json` → `docs/architecture/design/evidence/prototype-symbols.json`
- `docs/architecture-design/reference/feedback-map.json` → `docs/architecture/design/reference/feedback-map.json`
- `docs/architecture-design/reference/path-only-artifact-decision.md` → `docs/architecture/design/reference/path-only-artifact-decision.md`
- `docs/architecture-design/reference/previous-prototype-inspection.md` → `docs/architecture/design/reference/previous-prototype-inspection.md`
- `docs/architecture-design/reference/runner-and-handler-contract.md` → `docs/architecture/design/reference/runner-and-handler-contract.md`
- `docs/architecture-design/reference/stage-handler-map.json` → `docs/architecture/design/reference/stage-handler-map.json`
- `docs/architecture-design/sources.md` → `docs/architecture/design/sources.md`
- `docs/architecture-design/stage-library-map.md` → `docs/architecture/design/stage-library-map.md`
- `docs/spec-data/README.md` → `docs/governance/spec-data/README.md`
- `docs/spec-data/lifecycle-contracts.json` → `docs/governance/spec-data/lifecycle-contracts.json`
- `docs/spec-data/process-matrices.json` → `docs/governance/spec-data/process-matrices.json`
- `docs/spec-data/requirements.json` → `docs/governance/spec-data/requirements.json`
- `docs/spec-data/stateful-scenarios.json` → `docs/governance/spec-data/stateful-scenarios.json`

Остальные отложенные переносы и их потребители перечислены в принятом инвентаре Task 0006.
Он является входом планирования, а не заменяет текущую проверку Git перед изменением пути.
