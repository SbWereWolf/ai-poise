# SQLite и хранение DDD-02

Обновлено: 2026-09-06T17:36:12+05:00.

Схема SQLite **3**, project schema **ddd-task-content-2**. Предыдущие схемы не читаются и не мигрируют. Требуется отдельный пустой state root. SQLite, локальный внешний flock и короткие транзакции остаются существующими инфраструктурными механизмами.

| Таблица | Владелец и смысл |
|---|---|
| tasks/submissions/section_layers/task_events/task_results | прежняя модель Task и неизменяемых результатов |
| content_contracts(task_id,version,data) | снимки объединённых goal/task определений, запись только TaskRepository |
| trace_point_layers(submission_id,task_id,route_id,point_id,data) | новые значения и tombstones точек; составной FK к собственному submission |
| task_methods(task_id,method_id,version,data) | неизменные точные методы и явное расписание; FK к задаче |

Текст дополнительных секций по-прежнему хранится только в section_layers. Идентичный повтор не добавляет слои; A→B→A является новой работой. Route/checkpoint definitions хранятся внутри проверенного JSON снимка policy: целостность их внутренних ссылок проверяет доменная библиотека, а не вымышленный FK к полю JSON. Между task/submission/layers действуют реальные составные FK.

Сохранение root, submission, sections, traces, новых методов, contract snapshot и events происходит одной короткой транзакцией. Реальный SQLite trigger в тесте прерывает запись event: все изменения пакета откатываются. Тестовые команды и Git работают вне этой транзакции/lock.

Rehydration получает последние значения по индексам и ROW_NUMBER для каждой секции/точки. Current projection не является второй редактируемой копией текстов. Старый metadata-контракт сохраняет начальные данные; query заполняет текущий реестр методов из task_methods, включая зарегистрированные позднее.

Артефакты остаются файлами под owner root. Кандидатные ArtifactFact получаются от path inspector; в Task/engine не добавлен доступ к filesystem. Предыдущая инфраструктура `artifacts` пока не полностью извлечена в конечный контекст DDD.
