# Исходные API-метрики AI-poise

Обновлено: 2026-09-16.

## Сохранение фактов источника без вычисления длительностей

Необязательное поле `telemetry.source_metrics` передаёт доступные наблюдения API в
существующий `OptionalTelemetry` → dispatcher/spool → `SqliteAccounting`. Оно не
создаёт второй accounting store, счётчик сообщений или обязательный шаг Task.
Сохранение выполняется в worker, в том же envelope и той же optional транзакции.

Каждое наблюдение явно задаёт `source` (из accounting.sources), `stream`, `event_id`,
`observed_at` (ISO datetime с часовым поясом) и `parameters` (объект конечных JSON
значений). Список ограничен `accounting.max_events`; при включённом spool также
действуют его byte/capacity limits. Отсутствующий источник или timestamp не заменяется
текущим временем. Некорректная optional метрика приводит к диагностике доставки,
но не отменяет корректный результат основной операции.

```json
{
  "usage": [],
  "intervals": [],
  "cause": null,
  "finding_targets": [],
  "source_metrics": [
    {
      "source": "configured-api",
      "stream": "request-stream",
      "event_id": "response-42",
      "observed_at": "2026-09-16T01:02:03.456+05:00",
      "parameters": {"duration_ms": 12.5, "vendor_usage": {"actual_tokens": 7}}
    }
  ]
}
```

Это пример формы, не фактическое измерение и не инструкция получать недоступные
значения. Передаются только разрешённые метрики, не prompt, response body, секреты
или произвольный HTTP payload. Названия и единицы исходных параметров не меняются.
Отсутствующее поле, `null` и измеренное `0` остаются различимыми. API duration
сохраняется буквально; сборщик не достраивает start/end и не вычисляет разницу
между временем источника, capture или persistence. Поздняя и неупорядоченная
доставка метрик допустима; исходный timestamp сохраняется вместе со смещением.

`started`/`finished` envelope и `enqueued_at`/`next_attempt_at` spool — отдельные
технические наблюдения, не замена `observed_at`. Идемпотентность доставки относится
к identity полного envelope. Совпадение source event ID в разных envelope не
объявляется дедупликацией исходных API events: их агрегация и производные вычисления
принадлежат аналитике. Существующие `usage` counters и явно reported `intervals`
сохраняют отдельный прежний контракт; raw metrics не добавляются к ним повторно.

Данные доступны в прежней read projection `snapshot()['telemetry']`. Автоматическое
подключение внешнего API, универсальный collector для чужих приложений и полнота
наблюдений здесь не заявляются. Пределы доставки и окно потери до сохранения worker
описаны в [асинхронной доставке](telemetry-delivery.md#асинхронная-доставка-без-блокирования-работы).
