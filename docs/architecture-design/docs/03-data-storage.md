# Хранение данных и доступ к ним

Создано: **2026-09-06T16:04:53+05:00**.

Статус: **логическая модель хранения; не SQL DDL и не миграция**.

## 1. Что является источником истины

Одна SQLite хранит корни задач/спринтов, тексты секций, слои, типизированные business records, связи, сессии, методы, receipts и метрики. Файлы содержат raw output, изображения, другие артефакты и пакеты передачи. Git целевых codebases хранит код/тесты/документацию продукта. Он не заменяется копиями этих файлов в секциях задачи.

Это разделение сохраняет последнее решение пользователя: **section — текст в БД; artifact — файл**. Не возвращаем file-backed sections или giant `tasks.data` как единственный источник всего состояния.

Имя таблицы не обязано совпадать с агрегатом. Несколько typed доменных объектов можно отображать в общий технический формат, если тип, состояние и связи проверяются строго. Но публичный домен при этом не становится CRUD над произвольными словарями.

## 2. Логический каталог таблиц

Ниже эскиз хранения для целевой модели. Он задаёт владельцев, отношения и основные индексы. Это **не требование создать все таблицы до первого рабочего маршрута**, не готовый SQL и не обещание, что точные имена не уточнятся при реализации. DDL должен реализовать перечисленные обязательства без скрытых defaults.

| Таблица | Владелец записи | Назначение | Ключи и индексы |
|---|---|---|---|
| `store_meta` | configuration | Идентичность store и единственная поддерживаемая версия формата | store_id PK |
| `config_snapshots` | configuration | Неизменные полностью разрешённые процессы и execution policies | digest UNIQUE |
| `projects` | configuration | Стабильная идентичность проекта отдельно от его локальных manifest snapshots | project_id PK |
| `project_bindings` | configuration | Неизменные явно принятые project/codebase/runtime mappings без секретных значений | binding_id PK; project_id, binding_id UNIQUE |
| `owners` | owner infrastructure | Общий FK-якорь владельца текстов и файлов: task, sprint, runtime | owner_id PK; owner_id, kind, project_id UNIQUE |
| `tasks` | tasks | Корень задачи: контракт, версия, lifecycle, текущий подход и последний итог | task_id PK; project_id, lifecycle; goal_type, lifecycle |
| `sprints` | sprints | Корень спринта и указатель его опубликованного плана/результата | sprint_id PK; project_id, lifecycle |
| `sprint_memberships` | sprints | Единственное место принадлежности task спринту | sprint_id, task_id PK; task_id UNIQUE |
| `sprint_dependencies` | sprints | DAG и явный контракт доступности predecessor | sprint_id, predecessor_id, successor_id UNIQUE; sprint_id, successor_id |
| `stage_iterations` | tasks | Попытка содержательного этапа и фазовая позиция | task_id, stage_key, iteration_no UNIQUE; task_id, phase |
| `submissions` | tasks | Неизменные содержательные пакеты PREPARE/CONTINUE и их результаты валидации | iteration_id, phase, payload_digest UNIQUE |
| `stage_results` | tasks | Неизменный зафиксированный результат этапа/задачи | iteration_id, result_id; result_id PK |
| `sections` | content via owner repository | Идентичность обычной текстовой секции и указатели актуальных слоёв | owner_id, section_key UNIQUE |
| `section_revisions` | content via owner repository | Текст каждого слоя секции без перезаписи предыдущего | section_id, revision_no UNIQUE; section_id, revision_id UNIQUE |
| `work_records` | typed owner repository | Идентичность first-class requirement/DoD/method/obligation/finding/resolution/evidence/decision | owner_id, kind, business_key UNIQUE; owner_id, kind |
| `record_revisions` | typed owner repository | Версии типизированного business record и индексируемые поля | record_id, revision_no UNIQUE; owner_id, state, outcome; record_id, revision_id UNIQUE |
| `record_links` | content via owner repository | Трассировка и типизированные связи между конкретными версиями | from_revision_id, to_revision_id, relation_kind UNIQUE; to_revision_id, relation_kind |
| `workspaces` | workspaces | Рабочее дерево конкретной работы по codebase | workspace_id PK; runtime_binding, checkout_path UNIQUE; owner_id, codebase_id |
| `workspace_subjects` | workspaces | Снимки проверяемого состояния: task base, entry, current, verified | subject_id PK; workspace_id, observation_phase |
| `sessions` | sessions | Внутренняя сессия и текущая формальная работа | session_id PK; adapter, external_session_id, agent_instance_id UNIQUE where applicable |
| `work_cycles` | sessions | Рабочий цикл для одного поручения и максимум одной задачи | session_id, cycle_id; task_id, iteration_id |
| `operations` | operations | Намерение составного действия и известное состояние выполнения | request_key UNIQUE; cycle_id, state |
| `operation_steps` | operations | Шаги внешних и транзакционных действий | operation_id, sequence UNIQUE; operation_id, state |
| `executions` | executions | Попытка точного запуска над определённым предметом | execution_id PK; operation_step_id, execution_id; owner_id, state |
| `execution_receipts` | executions | Неизменный фактический outcome без статуса успешности задачи | execution_id, receipt_id UNIQUE |
| `record_execution_refs` | verification via owner repository | Связь evidence record с реальной попыткой/receipt | evidence_revision_id, receipt_id PK |
| `artifacts` | artifacts | Стабильная идентичность path-only артефакта в области владельца | owner_id, canonical_relative_path UNIQUE |
| `artifact_snapshots` | artifacts | Зафиксированная версия файла и её внутренние технические факты | artifact_id, digest UNIQUE; snapshot_id PK |
| `artifact_links` | artifacts via owner coordinator | Ссылка результата/доказательства на конкретный snapshot | snapshot_id, referring_owner_id; record_revision_id |
| `background_jobs` | operations | Ограниченная очередь materialize/metrics и других non-critical работ | job_id PK; state, job_id |
| `usage_events` | metrics | Исходные reported counters и completeness | source, source_event_id UNIQUE; cycle_id, at |
| `work_intervals` | metrics | Активное время агента/блокирующих операций, отдельно ожидание пользователя | source_event_id UNIQUE; cycle_id, starts_at |
| `benefit_entries` | metrics | Зачёт/отзыв конечной полезной работы | event_key UNIQUE; owner_id, at |
| `incidents` | observability | Баг/нарушение согласованности Harness, влияющее на доклад | operation_id, incident_id; owner_id, at |
| `transfers` | operations | Фиксация snapshot/списка файлов/области переноса и receipt | transfer_id PK; operation_id, transfer_id |

Подробности каждого набора ключей и FK приведены в [JSON-каталоге](../data/storage-catalog.json). JSON здесь — спецификация таблиц, а не действующая БД или migration.

## 3. Почему для части содержательных записей общий storage

`work_records` + `record_revisions` переиспользуют хранение requirement, DoD, method, obligation, finding, resolution, manual evidence и decision. Поле kind выбирает **явно зарегистрированную schema**, но в коде работают отдельные модели `VerificationMethod`, `Finding`, `ResolutionProposal`, `UserDecision`, а не `dict` с любыми полями.

Это не слепое EAV-хранилище и не один JSON задачи. В таблицах отдельно находятся owner, kind, version, state/outcome и связи. Статусы, текущие refs, counters запросов и связанные evidence доступны без разбора всех task payloads. JSON используется только для содержательной формы конкретного record, варьирующейся по независимым goal-type schemas.

Ссылки внутри собственных данных хранятся как реальные FK в `record_links` и специализированных связующих таблицах, а не только как строки внутри JSON. Домен дополнительно проверяет допустимые типы концов: existence FK не доказывает, что ссылка на decision является ссылкой на method. External requirement/file refs являются версионированными Value Objects и разрешаются через project/workspace mapping; SQL не удостоверяет наличие внешнего файла.

Ручное evidence имеет форму проверяемого аргумента. Командное evidence связывается с `execution_receipts` через отдельную FK-таблицу. Нельзя «заполнить exit_code» для логического доказательства, чтобы пройти универсальную схему.

## 4. Referential integrity и владелец

Общий `owners` нужен, чтобы section/artifact не ссылались на непроверяемую пару `owner_type/owner_id`. Задача, спринт и runtime-сессия создают свой owner в одной транзакции со своим typed root. Отдельной команды «создать произвольного owner» нет.

Формат БД должен обеспечивать соответствие kind root-таблице составными ключами/FK и CHECK. На commit дополнительно проверяется, что каждый создаваемый owner имеет ровно один соответствующий typed root. Это важно: один обычный FK от tasks к owners сам по себе не доказывает существование обратной строки Task для каждого owner.

`projects` хранит стабильный project_id, а `project_bindings` — отдельные неизменные локальные привязки. Work cycle/workspace ссылаются на конкретный binding; смена текущего manifest не переписывает прежнюю среду исполнения в evidence.

В `sprint_memberships` находится **единственное** mutable отношение Task→Sprint; в tasks не хранится отдельно редактируемая копия `sprint_id`. У двух концов `sprint_dependencies` составные FK на `(sprint_id, task_id)`, что запрещает ссылку за пределы этого sprint. Принадлежность одному project проверяется составными FK к уникальным parent keys. Циклы графа и содержательная пригодность зависимости проверяются доменом Sprint, не магией SQL.

Для current pointers используются составные FK: current section revision принадлежит той же section, current record revision — тому же record, iteration — той же task. При создании взаимных ссылок допустимы явно заданные отложенные FK либо порядок вставки с последующей установкой pointer; это не поле по умолчанию и не пользовательская миграция. Все обязательные поля задаются явно.

Физическое удаление принятой Task/Sprint и cascade удаления их результатов в обычном workflow отсутствует. Отмена является бизнес-решением, а не `DELETE`. Retention/cleanup работают по отдельным разрешённым правилам и не должны уничтожать referenced evidence.

SQLite умеет PK/UNIQUE/CHECK/FK, но режим FK нужно включить и проверить на каждом соединении; спецификация индексов должна учитывать child FK columns [S7, S11](../sources.md). Никаких заявлений, что foreign keys проверят смысл аргумента или существование файла.

## 5. Секции и кандидатное состояние

`sections` хранит исходный rendered template и указатели working/accepted. `section_revisions` хранит обычный `TEXT`, digest и исходную принадлежность. Старый слой не редактируется; исправление создаёт новый слой.

При `verify` новый submission сначала формирует **кандидатную проекцию**. Prechecks используют её, а не прежние шаблоны в accepted projection. Структурно корректный кандидат сохраняется до долгих checks и виден как незавершённая работа. Failed tests не удаляют его. Невалидный payload сохраняется, если нужен для разбора, как отклонённый submission; он не изменяет working pointers.

Verified result фиксирует конкретные refs слоёв и receipts. Пользовательское принятие обновляет accepted pointers отдельным решением. После verified нельзя незаметно заменить текст под тем же результатом: нужен предусмотренный rework. Статус `template` сравнивается с оригинальным шаблоном и normalization rule этой секции, не с новым файлом текущей версии пакета.

Точное число байтов/строк определяется библиотекой при создании слоя. Reader возвращает диапазон и общие размеры, не число токенов модели. Дополнительные sections и старые итерации читаются адресно.

## 6. Методы чтения

Query adapter может делать JOIN нескольких принадлежащих одному store таблиц через документированные read contracts. Это не даёт ему права UPDATE чужих таблиц. Доменный Repository не используется для построения каждого сводного экрана.

Основные проекции:

| Проекция | Читает | Что не загружает |
|---|---|---|
| Task context | task/iteration headers, chosen section layers, active typed records, current evidence refs | Все старые layers и raw stdout |
| Sprint overview | sprint, memberships, компактные task progress facts, dependency readiness | Полные тексты 2000 задач |
| Current findings | owner/kind/current revision indexes, linked evidence summaries | Другие задачи без нужной связи |
| Section range | Один revision TEXT и его size metadata | Остальные секции |
| Metrics by period/type | usage, intervals, benefit entries, immutable classification | Conversation transcripts |

Чтение выполняется в коротком consistent read snapshot. SQL values параметризуются; имена таблиц/колонок задаёт mapper, не аргумент пользователя. Read-only access использует соответствующий режим connection. Не разрешается выполнять arbitrary SQL из CLI. Python sqlite3 предоставляет необходимые средства параметризации и управления транзакцией [S6](../sources.md).

Изначально status и eligible views строятся запросами и доменной функцией над компактными фактами. Не вводим независимо редактируемые `tasks_count`, `eligible_count` и текст `progress`. Если позже понадобится materialized cache, он имеет источник/версию и восстанавливается; это не второй источник истины.

## 7. Методы записи и внешний lock

Все записи проходят через application use case, доменную команду и repository. Короткий UoW использует один connection для всех своих mappers. `repository.persist()` не выполняет скрытый commit.

Последовательность локальной записи:

```text
получить единый configured external state lock
→ открыть connection и проверить обязательные режимы
→ BEGIN
→ прочитать/проверить versions и affected scope
→ применить domain ChangeSet через owner mappers
→ записать operation result и нужные события/jobs
→ COMMIT
→ закрыть connection и снять lock
```

Для простоты первой реализации чтение тоже использует краткий shared lock того же файла. Writer получает exclusive lock. Это исключает необходимость строить на SQLite busy retries координацию нескольких agents. Сам SQLite продолжает выполнять свою штатную транзакционную защиту; она не отключается и не считается заменой внешнему lock [S8, S9](../sources.md).

Путь lock — явно configured и общий для экземпляров одного store, предпочтительно рядом с БД внутри Harness state. Lock-файл не удаляется после каждой операции и не трактуется по факту существования; удерживается файловая блокировка. Таймаут, интервал ожидания и бюджет — обязательные конфиги. Поддерживаем локальный Linux/WSL filesystem; работоспособность блокировок на произвольном сетевом диске не обещаем.

Никакая transaction/DB lock не охватывает subprocess, IDE-вызов, ожидание пользователя, локальную Git-интеграцию, hashing большого файла или отправку backup. Git push запрещён. После внешней работы UoW заново читает relevant versions и subject before accepting outcome.

## 8. Граница агрегата и транзакции

Обычно одна доменная mutation меняет один root и принадлежащие ему дочерние записи. Синхронно обновлённая read projection не превращается во второй независимый агрегат. Завершение Task делает новые факты доступными SprintQueries сразу.

Внутри одного контекста WorkManagement допускаем **явно определённые в use case многокорневые локальные UoW**: публикация sprint с новыми tasks; согласованная отмена выбранной ветви; принятие последнего task result с изменением membership/решением закрытия sprint, когда все предпосылки уже проверены. Каждый root валидирует свои изменения через свой API, coordinator не пишет чужие поля напрямую.

Это сознательное практическое решение для одной SQLite и требования атомарной публикации, а не требование DDD «всегда менять все агрегаты вместе». Привязка Task к WorkSession и её освобождение также могут фиксироваться совместно в одном локальном UoW через методы обоих владельцев. Запись operation metadata не требует отдельного tool call. Область таких изменений перечисляется в соответствующем use case; это не право произвольного доступа ко всем roots. Для действий с внешними эффектами используются отдельные операции и receipts. Если closure sprint требует дополнительных проверок/передачи, completed Task остаётся фактом, а Sprint имеет незавершённый closure operation и не объявляется completed преждевременно.

## 9. Файловые области

Все расположения задаются config относительно Harness root. Ниже только условная схема, а не встроенные пути:

```text
<harness-root>/
  config/                         # все configs Harness, включая projects
  <configured-state-root>/        # gitignored
    <database-file>
    <state-lock-file>
    tasks/<task-id>/               # task artifacts, output/evidence snapshots
    sprints/<sprint-id>/            # общие файлы sprint
    runtime/<session-id>/          # result templates, временные files
    operations/<operation-id>/     # технический operational journal, не business artifacts
    transfer/<transfer-id>/        # явно готовящийся transfer package
```

Расположения owners и технических каталогов задаются конфигом без взаимного неоднозначного пересечения. Artifact API принимает только runtime текущей session, task текущей работы или её sprint. Технические каталоги не становятся четвёртым scope произвольного артефакта агента.

Рабочие деревья других codebases находятся по project manifest. Git deliverables остаются в них и учитываются как WorkspaceResultRef. Нельзя потребовать зарегистрировать `src/foo.py` как Harness artifact, если файл принадлежит worktree, а не одному из трёх artifact roots.

## 10. Path-only регистрация

Вход агента — только пути. ID, реальный owner, относительный путь, size и digest определяет библиотека. Не требуются purpose, тип, semantic declaration, row IDs задачи или timestamps.

Алгоритм:

```text
разрешить permitted owner roots из binding
→ проверить существование обычного файла и canonical path
→ проверить принадлежность ровно одному разрешённому root
→ получить owner + relative path
→ определить существующий ArtifactId или назначить новый
→ измерить технические факты вне DB lock
→ внутри короткого UoW проверить owner/version и записать refs
```

Повтор одного owner-relative пути не создаёт второй ArtifactId и не увеличивает count. При переносе cloud/local absolute prefix меняется, owner-relative identity сохраняется. Выход показывает ID и актуальный путь; внутренний digest не становится обязанностью LLM.

Если конкретная версия файла входит в принятое evidence, её bytes должны быть зафиксированы внутри **того же** owner root. Изменение рабочего пути не меняет прошлое evidence: сохраняется новый snapshot под тем же ArtifactId, старый остаётся доступным по snapshot ref. Политика имён snapshot-каталога явна в config. Это техническая гарантия сохранности, а не новая форма ввода.

Никакой регистрации с автоматическим переносом из runtime в task по догадке. Агент размещает durable result сразу в task/sprint root. Harness-owned outputs проверок создаются у владельца либо переносятся туда при финализации по явной output policy, не по семантической эвристике.

Count rules считают уникальные текущие ArtifactId, прошедшие scope/path pattern. Несколько snapshots одного файла не являются несколькими тестами. Подсчёт тест-кейсов внутри кода поручается точному project check; универсальный semantic detector не проектируется.

## 11. Связка БД и файлов без ложной атомарности

SQL-транзакция не откатывает filesystem и удалённый Git. Поэтому файловая публикация имеет короткий явный протокол:

```text
подготовить/зафиксировать файл вне DB lock в owner-root staging
→ получить измеренные факты
→ UoW: зарегистрировать pending запись и intent
→ опубликовать готовое имя файла внутри того же root
→ UoW: подтвердить snapshot ready и связать с business result
```

Чтение не возвращает pending как ready. Сбой оставляет конкретную незавершённую operation; повтор проверяет фактический файл и digest, а не создаёт второй артефакт. Невозможно обещать общую ACID-транзакцию SQLite+filesystem+Git; подтверждённый факт SQL и неизвестный внешний эффект отображаются раздельно [S12](../sources.md).

## 12. Cleanup и представления tool output

Обработчик известной команды один, с режимами primary и materialize. Raw result immutable; primary синхронный, подробные представления создаются асинхронно. Имена файлов и профили задаются config, unknown commands используют один явно зарегистрированный generic parser.

Перед успешным финальным докладом coordinator дожидается обязательных jobs, сохраняет нужные raw/представления у task/sprint или в предусмотренном result ad-hoc, собирает Incidents, заменяет runtime refs в ответе на durable refs и очищает runtime-only файлы. Нельзя вернуть ready-ссылку на уже удалённый runtime file.

State session/work_cycle в SQLite не является runtime artifact и может оставаться для продолжения/учёта после очистки каталога. Для ad-hoc, где нет durable task/sprint owner, не обещаем долговечную ссылку на raw runtime output: либо результат выдаётся пользователю целиком/в явный deliverable, либо временная ссылка перестаёт предлагаться после финализации.

## 13. Передача и совместимость

Физический пакет — SQLite snapshot нужной области и файлы владельцев с manifest/digest. Backup API создаёт согласованную копию БД, но не копирует внешние artifacts автоматически [S10](../sources.md). При capture фиксируется набор immutable snapshots, затем копируются соответствующие файлы; отсутствие referenced файла блокирует признание пакета готовым.

Whole-store замена допустима только для пустого/эксклюзивно переданного назначения. Для независимых локальных и cloud задач переносится явно выбранная owner-область с exact base revisions; destination не получает silent last-writer-wins. Неописанное содержательное слияние одной task отклоняется. Это не автоматический новый format migration.

Строки с абсолютными workspace paths не копируются как действующий destination binding: адреса codebase и roots явно разрешаются новым manifest. Бизнес-ID и owner-relative artifact refs сохраняются. Secrets не записываются в task/config snapshots/логах; raw outputs проходят явную политику обращения с чувствительными данными.

Версия schema должна точно совпадать с поддерживаемой. Инициализация пустого store и изменение старого store — разные операции. Миграции спринтов, задач и данных не проектируются/не исполняются без прямого разрешения пользователя.
