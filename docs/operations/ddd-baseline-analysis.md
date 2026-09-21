# TEST-DDD-BASELINE: разбор причин отказов

## Область и результат

Task `0163`, этап `classify`, 21 сентября 2026 года. Это диагноз существующей
адресной выборки, **не исправление тестов или продукта и не принятие Task**.
На исследованном коммите `ceac4863683eaaccc92e6d0b8adefa809eeb36fd`
(tree `a542769d9015cd310ff76a1b0d3190d0551e2c5c`) сохранены **48 исходов:
33 passed, 15 failed, 0 errors, 0 skipped**. Непосредственные причины всех 15
установлены; они разделены на шесть групп. Подтверждённого нового нарушения
продуктового контракта эти падения не демонстрируют: часть сценариев не доходит
до своей целевой проверки, другая опирается на прежние gate-ожидания.
Это не доказательство отсутствия дефектов за недостигнутыми границами.

Ни один исход не заменён успешным, ни один assertion/skip/validator не изменён.
Исполнительские предложения ниже не зарегистрированы как новые Task и не являются
сделанными исправлениями. Для исходных 15 — восемь независимых предложений;
девятое относится к отдельному отказу подтверждающей выборки.

## Доказательства и воспроизводимость

Исследован именно сохранённый Task worktree, а не более новый engine `a9fba66`.
Публичный `OBSERVE` с ID `b4f08a8d-3308-443d-843d-db89cf7e4d93` выполнил:

```sh
python3 -B -m pytest -vv --tb=short \
  tests/ddd/test_task_domain.py \
  tests/ddd/test_task_application.py \
  tests/ddd/test_content_gates.py
```

Фактические аргументы, окружение, commit/tree и завершение принадлежат сохранённому
native receipt; команда выше обозначает исследованную выборку, не указание
повторять завершённый запуск. Receipt фиксирует exit `1`, `passed=false`,
`capture_complete=true`, `source_unchanged=true`; commit до и после один и тот же.
Полный stdout — 27 981 байт, stderr — пустой. Повторное чтение их байтов и сравнение
ledger с обоими историческими JUnit выполнены в продолжении `008` без нового OBSERVE.

| Доказательство | SHA-256 / результат |
|---|---|
| Полный stdout исходного OBSERVE | `7ad11a70aac5cbe9817ee83582113ff198d7b448627245d9a9ad9a3c954bd261` |
| Пустой stderr | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `ordinary-task-baseline.xml` | `7a3e924060411a0e4f48ac8e8b5f8233c6f2d072e6c1412907e3ba5ea261f756` |
| `ordinary-task-domain.xml` | `fc0c2acef62f59097661ef9bd8366c307e300f0753afc878c421da6ecc4ee877` |
| Сопоставление каждого исторического JUnit с текущими исходами | Все 48 node/outcome совпали, расхождений нет |

Историческая пара относится к baseline до artifact reuse и его реализации; она
не подменяет текущее наблюдение на `ceac486`. Полный ledger сохранён в Task artifact
`diagnosis/baseline-outcomes.json`. Сырые stdout/stderr — в Task run с указанным
observation ID. В архиве продолжения присутствуют
`evidence-current/0163-history/`, `c007-0163-outcome-ledger.json`,
`c008-diagnosis-evidence-verification.json`, `c008-classification.json`.
Пути этого предложения обозначают содержимое checkpoint, не файлы source checkout.

`verified` этапа `reproduce` означает полноту предъявленного наблюдения:
`stage_outcome=not_satisfied` и отрицательный продуктовый результат сохранены.
Число успешных тестов нельзя увеличивать за счёт replay или повторного чтения receipts.
Полный suite не запускался и не объявляется зелёным.

## Контракт, с которым сопоставлены ожидания

[Точный StageContract Task](../workflows/content-requirements.md#точный-stagecontract-task)
разделяет **каталог** ContentPolicy и активные `entry_requirements`/`exit_requirements`.
Объявление предиката и его активация — разные действия. Базовая fixture в
[tests/conftest.py](../../tests/conftest.py) создаёт пустые refs, а helper
`setup` в [content-gate тестах](../../tests/ddd/test_content_gates.py) меняет
только content layers. `Task.assess_stage_content` передаёт именно выбранные refs
в `ContentPolicy.evaluate`; пустой список не означает «все правила».

Entry gate проверяется до claim/context и повторно до submit/artifact effects;
отказ этой границы не создаёт submission. Значение, впервые произведённое result
текущего этапа, нельзя считать его уже существующим entry prerequisite.
`content_additions` не расширяет активные gates без явной допустимой reviewer-ревизии.

[Достижимость trace](../workflows/content-requirements.md#достижимость-срока-trace-требования)
проверяется для **новых** контрактов/предикатов; первая запись в тот же due stage
допустима для `post`, но не для `pre`. Исторический `restore_layers` сохраняет
прежние слои без переписывания и не даёт права добавлять новые невозможные deadlines.
Новый artifact требует явного `source`, согласованного с реальным появлением файла.

В [runtime](../../src/poise/runtime.py) `_prepare_verification_commit` вызывается
до запуска checks, а receipts привязаны к этому локальному кандидатному commit.
Старое assertion «post-отказ оставляет HEAD без изменения» с этим порядком не
совместимо. Оно не эквивалентно нужной защите «нет verified/accepted для отрицательного
post gate». В разделе
[Последовательность verify](../workflows/content-requirements.md#последовательность-verify)
остаётся формулировка «не делать verified commit»; её нельзя читать как запрет
создать локальный кандидат до проверки. Неоднозначность текста отмечена в DDD-P04,
не исправлена молча в этой диагностике.

## Шесть непосредственных групп

| Группа | Непосредственная причина | Число исходных отказов |
|---|---|---:|
| G1 | Подготовка: уже существующий каталог | 1 |
| G2 | Предусловие публичного caller, включая отдельный пример | 3 |
| G3 | Устаревшие gate fixtures/ожидания явного StageContract | 6 |
| G4 | Невыполнимый срок нового trace pre-требования | 3 |
| G5 | Неполная схема происхождения нового artifact | 1 |
| G6 | Подготовка исторической Task нарушает один claim на сессию | 1 |

В G3 есть два разных исполнительских исправления: привязка заранее определённых
предикатов (DDD-P04) и проверка явной активации добавленных предикатов (DDD-P05).
В G2 две CLI-фикстуры отделены от самостоятельного Content demo. Это разделение
по фактическим контрактам и scope, а не создание искусственного Sprint.

## Полный перечень 15 отказов

Номера строк ниже относятся к исследованному `ceac486`. «Не доказано» отделяет
непосредственный отказ от исходной цели теста и последующих возможных проблем.

### Случай 01 — G1, DDD-P01

`tests/ddd/test_task_application.py::test_old_store_is_rejected_without_migration`

**Наблюдение:** FileExistsError на path.parent.mkdir(), до проверки прежней БД.

**Причина:** Подготовка повторно создаёт state/, уже созданный fixture Requirements Registry.

**Код:** `tests/ddd/test_task_application.py:116–126; tests/conftest.py:75–82,220.`

**Граница доказательства:** Не достигнуты отказ чтения старой схемы и сравнение исходных байтов БД.

### Случай 02 — G2, DDD-P02

`tests/ddd/test_task_application.py::test_cli_reads_section_without_repeating_task_id`

**Наблюдение:** CLI exit 2 вместо 0: No native caller identity.

**Причина:** Фикстура передаёт POISE_SESSION=S1; публичная граница принимает native identity либо постоянный абсолютный POISE_CALLER_BINDING, но не этот заменитель.

**Код:** `tests/ddd/test_task_application.py:279–287; src/poise/infrastructure/session_establishment.py:68–97.`

**Граница доказательства:** Чтение своей section без повторного task_id не проверено: CLI не получил caller.

### Случай 03 — G3, DDD-P04

`tests/ddd/test_content_gates.py::test_pre_gate_uses_new_candidate_and_blocks_commands_until_section_filled`

**Наблюдение:** verified вместо content_requirements_failed.

**Причина:** setup меняет ContentPolicy, но у Task остаются пустые entry/exit refs. Кроме того, попытка впервые наполнить entry requirement результатом того же этапа не соответствует pre-effect entry gate.

**Код:** `tests/ddd/test_content_gates.py:18–35,55–67; tests/conftest.py:304–309; src/poise/modules/tasks/domain.py:618–632.`

**Граница доказательства:** Не доказан обход активного gate. Ожидание submission_count=1 при entry-отказе также устарело; проверка нового контракта требует отсутствия submission.

### Случай 04 — G3, DDD-P05

`tests/ddd/test_content_gates.py::test_declare_fill_and_gate_extra_section_in_one_verify_no_register_call`

**Наблюдение:** any(rationale-required в due) == False после rework.

**Причина:** content_additions регистрирует предикат, но не активирует его в StageContract. Тест ожидает скрытую активацию.

**Код:** `tests/ddd/test_content_gates.py:70–81; src/poise/modules/tasks/domain.py:618–632.`

**Граница доказательства:** Регистрация/наполнение уже прошли; неуспешно именно ожидание автоматического gate на последующем входе.

### Случай 05 — G4, DDD-P06

`tests/ddd/test_content_gates.py::test_full_trace_two_routes_future_document_then_methods_then_review`

**Наблюдение:** DomainError product-now: phase=pre; earliest required stage=tests.

**Причина:** Точка впервые writable на tests, но нужна до входа в tests. Новый контракт отклонён до создания сценария.

**Код:** `tests/ddd/test_content_gates.py:38–52,93–114; src/poise/modules/content_requirements/domain.py:213–220,341–367.`

**Граница доказательства:** Не достигнуты переходы planned/documented, поздний verdict, независимость цепочек и чтение раннего слоя. Другие same-stage pre правила helper тоже требуют пересмотра по их действительному сроку.

### Случай 06 — G3, DDD-P04

`tests/ddd/test_content_gates.py::test_post_gate_runs_after_checks_but_cannot_mark_verified_or_commit`

**Наблюдение:** verified вместо content_requirements_failed.

**Причина:** rationale-required не включён в exit_requirements. После исправления привязки останется отдельное устаревшее ожидание неизменного HEAD: локальный кандидат теперь коммитится перед checks.

**Код:** `tests/ddd/test_content_gates.py:117–126; src/poise/runtime.py:713–737,1404–1408.`

**Граница доказательства:** Активный post gate не проверен. Наличие кандидатного коммита не даёт verified/accepted; запрет их получения сохраняется.

### Случай 07 — G5, DDD-P07

`tests/ddd/test_content_gates.py::test_artifact_cardinality_by_stage_uses_existing_path_only_interface`

**Наблюдение:** BatchValidationError: точный набор полей artifact требует source.

**Причина:** Новый artifact-предикат не объявляет происхождение файлов. Ошибка schema возникает до создания Task и подсчёта путей.

**Код:** `tests/ddd/test_content_gates.py:129–137; src/poise/modules/content_requirements/domain.py:37–50,291–293.`

**Граница доказательства:** Не проверены уникальность путей и прохождение для двух разных существующих файлов. Пустой entry gate и создание входных файлов только после bootstrap также должны быть согласованы с выбранным source.

### Случай 08 — G3, DDD-P04

`tests/ddd/test_content_gates.py::test_missing_required_content_does_not_block_user_cancel`

**Наблюдение:** checks_failed вместо content_requirements_failed.

**Причина:** Предикат не связан с entry gate; verify дошёл до RED без add_test и завершился отказом checks раньше ожидаемой ветки.

**Код:** `tests/ddd/test_content_gates.py:140–144; tests/conftest.py:304–309; src/poise/modules/tasks/domain.py:618–632.`

**Граница доказательства:** Сам cancel ещё не вызван. Этот отказ не доказывает, что cancel требует заполненного содержимого.

### Случай 09 — G4, DDD-P06

`tests/ddd/test_content_gates.py::test_new_content_and_trace_are_atomic_with_submission`

**Наблюдение:** DomainError product-now вместо sqlite3.IntegrityError(test-content-fault).

**Причина:** trace_policy добавляет недостижимое новое pre-требование; валидация отклоняет его до целевой записи task_events.

**Код:** `tests/ddd/test_content_gates.py:38–52,147–158; src/poise/modules/content_requirements/domain.py:341–367.`

**Граница доказательства:** Инъекция отказа транзакции не достигнута; атомарность content/trace/submission этим падением не проверена.

### Случай 10 — G6, DDD-P08

`tests/ddd/test_content_gates.py::test_repository_rehydrates_historical_invalid_schedule_without_rewriting_it`

**Наблюдение:** sqlite3.IntegrityError: UNIQUE constraint failed: tasks.claimed_by.

**Причина:** replace меняет только task_id, сохраняя claimed_by активной T1. Создание LEGACY пытается назначить той же сессии вторую Task.

**Код:** `tests/ddd/test_content_gates.py:180–202; src/poise/infrastructure/sqlite/database.py:49.`

**Граница доказательства:** До повторной загрузки LEGACY дело не дошло. Ошибка не является отказом исторического restore_layers и не разрешает удалять unique index.

### Случай 11 — G3, DDD-P04

`tests/ddd/test_content_gates.py::test_domain_mark_verified_cannot_bypass_content_gates`

**Наблюдение:** checks_failed вместо content_requirements_failed.

**Причина:** Пустые gate refs; verify без add_test достигает RED. Первое assertion падает до прямого Task.mark_verified.

**Код:** `tests/ddd/test_content_gates.py:205–211; src/poise/modules/tasks/domain.py:618–632.`

**Граница доказательства:** Предполагаемый обход доменной защиты не воспроизведён. Нужен допустимый сценарий с реально активным неудовлетворённым gate.

### Случай 12 — G4, DDD-P06

`tests/ddd/test_content_gates.py::test_method_can_be_registered_with_trace_in_same_stage_result_and_is_executed`

**Наблюдение:** DomainError method-required: write_stages=[tests], due_stages=[tests], phase=pre.

**Причина:** Новый метод/точка впервые появляются в result этапа tests, но правило требует их до входа в тот же этап.

**Код:** `tests/ddd/test_content_gates.py:227–252; src/poise/modules/content_requirements/domain.py:341–367.`

**Граница доказательства:** Не достигнуты исполнение ADDED, точный replay, единственная регистрация и сохранение расписания code_review.

### Случай 13 — G2, DDD-P02

`tests/ddd/test_content_gates.py::test_cli_content_failure_is_business_failure_not_exit_zero`

**Наблюдение:** CLI exit 2 вместо 1: No native caller identity.

**Причина:** Тот же неподдержанный POISE_SESSION вместо установленной публичной identity. После исправления caller остаётся необходимость действительно активировать content gate.

**Код:** `tests/ddd/test_content_gates.py:255–263; src/poise/infrastructure/session_establishment.py:88–97.`

**Граница доказательства:** Граница business failure не достигнута. Нельзя заменить ожидаемый exit 1 на exit 2 или проверять только ненулевой код.

### Случай 14 — G3, DDD-P05

`tests/ddd/test_content_gates.py::test_accepted_result_cannot_hide_unsatisfied_newly_registered_goal_requirements`

**Наблюдение:** checks_failed вместо content_requirements_failed.

**Причина:** Новая секция/предикат добавлены в каталог без изменения StageContract; отсутствует автоматическая активация, на которую рассчитан тест. Дополнительно RED не подготовлен.

**Код:** `tests/ddd/test_content_gates.py:326–333; src/poise/modules/tasks/domain.py:618–632.`

**Граница доказательства:** До accept и нового bootstrap дело не дошло. Проверять надо явную активацию разрешённой reviewer-ревизией и запрет принять неудовлетворённый активный gate, а не скрытое расширение frozen contract.

### Случай 15 — G2, DDD-P03

`tests/ddd/test_content_gates.py::test_content_demo_cli_route_with_feedback`

**Наблюдение:** Пример exit 1; внутренний bootstrap отклонён: No native caller identity.

**Причина:** examples/content_demo.py использует POISE_SESSION=content-demo без публичного caller binding. Непосредственный отказ — до сценария, а не по итогам review.

**Код:** `tests/ddd/test_content_gates.py:336–344; examples/content_demo.py:17–34; src/poise/infrastructure/session_establishment.py:88–97.`

**Граница доказательства:** Не проверены completed, base_unchanged, doc_in_task_worktree и ровно два content-отказа. Статическое чтение также показывает наследуемую identity и изменения process после материализации task stage_contracts; это последующие риски, не дополнительно воспроизведённые отказы.

## Адресные подтверждения и дополнительный отказ

В продолжении `008` до документальных правок на неизменном `ceac486` выполнены
24 самостоятельных case из выбранных owning tests: **23 passed, 1 failed,
0 errors, 0 skipped**, exit `1`. Полные argv/JUnit/stdout сохранены как
`c008-corroboration-command.json`, `c008-corroboration.xml`, `c008-corroboration.log`.
Это отдельная выборка; её нельзя прибавлять к исходным 48 или назвать целиком зелёной.

Двенадцать тестов [Task StageContract](../../tests/tasks/test_stage_contracts.py)
подтвердили точные refs/phase, source dominance, rework paths, writable scope,
independent producer exit gate и ограничения read-only этапа. Четыре выбранные
проверки [runtime StageContract](../../tests/runtime_services/test_stage_contract_runtime.py)
прошли: entry failure до claim/context; сохранение pre-effect counters при entry
rejection; post gate видит outputs; content addition не меняет refs и не портит reload.
Три выбранные проверки [caller boundary](../../tests/runtime_services/test_session_establishment.py)
прошли: постоянная generated identity, публичный direct work с binding, отказ от
`POISE_SESSION` как заменителя. Две выбранные проверки
[ContentPolicy](../../tests/ddd/test_content_requirements.py) подтвердили same-stage
post/историческое чтение и запрет нового недостижимого добавления. Две проверки
[exact-commit proof](../../tests/runtime_services/test_commit_bound_verification.py)
прошли: checks выполняются на публикуемом candidate commit; новый commit с тем же
tree требует нового failed observation.

Отдельно отказал
`tests/runtime_services/test_stage_contract_runtime.py::test_reviewer_revision_changes_gate_refs_atomically`:
`DecompositionError: phases mismatch ... unknown=['code_review', 'tests']`.
Helper `_configure(inspection=True)` сокращает route до `test_review, implementation`,
но оставляет decomposition фаз исходного четырёхстадийного процесса. Отказ происходит
в `FocusedDecomposition.require_valid` до создания Task и до reviewer revision.
Это дополнительный fixture blocker, **не шестнадцатый исход внутри исходных 48**,
и не подтверждение отказа самой операции ревизии. Коррекция выделена в DDD-P09;
не менялась ни fixture, ни продуктовый валидатор. Успешный reviewer-revision runtime
тест в этом продолжении не заявляется.

## Самостоятельные предложения исправлений

Это **proposals, не Task IDs и не зарегистрированные задачи**. Scope задаёт границы
будущей работы, а checks — требуемое доказательство после исправления, не уже
полученный результат. Каждый scope остаётся самостоятельным; общий файл тестов
не является основанием для искусственной зависимости. Публикация/принятие proposals
должны пройти обычное публичное планирование, Requirements mapping и готовность.

### DDD-P01 — Изолировать legacy-store fixture

**Scope:** tests/ddd/test_task_application.py: test_old_store_is_rejected_without_migration

**Исправление:** Создать саму прежнюю Task DB в изолированной подготовке, учитывая уже созданный state/ и не заменяя Requirements Registry. Исправляется подготовка теста, не storage service.

**Приёмка:** Тест должен дойти до продуктового отказа старой схемы; сохранить точный байтовый before/after и запрет неявной миграции. Выполнить этот node и соседний уже успешный test_old_version_two_store_is_rejected_without_migration.

### DDD-P02 — Привести две CLI-фикстуры к текущей identity

**Scope:** Два CLI node из группировки G2: tests/ddd/test_task_application.py и tests/ddd/test_content_gates.py; только их подготовка и минимальный общий test helper при доказанной необходимости.

**Исправление:** Получить одну настоящую публичную identity для fixture-владельца и subprocess через абсолютный постоянный binding; изолировать наследуемые native/Poise сигналы. Не подменять ID готовой строкой S1 и не копировать чужой binding. Для content-сценария задать реальный gate и достоверное состояние до входа.

**Приёмка:** Сохранить успешное чтение section без task_id, exit 1 именно для content_requirements_failed и отсутствие command evidence при entry-отказе. Отдельно оставить отрицательную проверку неподдержанного POISE_SESSION. Общий ненулевой exit не заменяет ожидаемый business failure.

### DDD-P03 — Обновить самостоятельный Content demo

**Scope:** examples/content_demo.py, его локальная content-demo fixture при необходимости и tests/ddd/test_content_gates.py::test_content_demo_cli_route_with_feedback; не Evidence demo.

**Исправление:** Построить self-contained публичные callers, обязательное окружение и действительный Task-контракт до bootstrap. Проверить соответствие content predicates, stage refs, writable scopes, Requirements snapshot и сроков trace. Разделение синтетических ролей внутри демонстрации не выдавать за независимый инженерный review.

**Приёмка:** Полный пример должен сохранить completed, unchanged base, документ только в Task worktree, ровно два предусмотренных content-отказа и реальный feedback/rework. Выполнить точный demo node и явный CLI-запуск в изолированной папке; прочитать полный JSON report. Потенциальные следующие отказы сперва воспроизвести, не считать их уже доказанными.

### DDD-P04 — Проверять статические entry/exit gates, а не каталог

**Scope:** Четыре G3 node: pre gate, post gate, cancel, domain mark_verified; tests/ddd/test_content_gates.py. При необходимости синхронизировать неоднозначную старую последовательность в docs/workflows/content-requirements.md с уже реализованным exact-commit контрактом.

**Исправление:** В положительной подготовке явно связать предикат с нужным StageContract. Entry-предусловие должно существовать до входа; для проверки его повторной проверки создать доступный сценарий, затем изменить вход до verify. Содержимое, впервые произведённое текущим этапом, проверять достоверным post gate. Не менять production validators.

**Приёмка:** Entry-отказ не оставляет submission/artifacts/checks. Post-отказ сохраняет receipts и локальный candidate commit, но не даёт verified/accepted; HEAD receipts совпадает с проверенным кандидатом. Cancel доступен без удовлетворённого содержимого; прямой доменный mark_verified отвергает неудовлетворённый активный gate. Изменение устаревшего assertion должно сопровождаться именно этими содержательными гарантиями, а не сравнением с любым фактическим статусом.

### DDD-P05 — Разделить content registration и активацию gate

**Scope:** Два G3 node про content_additions: declare_fill_and_gate_extra_section и accepted_result_cannot_hide_unsatisfied_newly_registered_goal_requirements; tests/ddd/test_content_gates.py.

**Исправление:** Регистрация/наполнение одним пакетом сохраняются. Отдельно проверять, что новый predicate не изменяет frozen gate refs. Активация — только допустимая явная reviewer-ревизия с expected_version/request_id и реальным разделением роли; при непригодном frozen workflow нужен пересмотр новой fixture, не обход runtime.

**Приёмка:** Без ревизии каталог расширяется, refs прежние, replay не плодит слои. После разрешённой ревизии due содержит точный ID и неудовлетворённый активный gate запрещает verified/accept. Неверный actor/revision не меняет состояние. Сохранить неизменность существующих definitions.

### DDD-P06 — Сделать положительные trace-сценарии достижимыми

**Scope:** Три G4 node и их helper trace_policy в tests/ddd/test_content_gates.py.

**Исправление:** Для первой записи точки назначить реальный post deadline; последующий pre consumer должен читать ранее созданное доказательство. Связать действительные gate refs и расписание метода с маршрутом. Не расширять write_stages на случайные этапы ради формального пересечения. Негативные тесты invalid new trace оставить отрицательными.

**Приёмка:** Все три node достигают исходных целевых проверок: независимые trace-цепочки и история planned; rollback всех content/submission/trace слоёв на test-content-fault; исполнение ровно зарегистрированного ADDED, один registry row и replay без дублирования. При первом отказе точно фиксировать достигнутую границу, а не подменять отказ ожидаемым DomainError.

### DDD-P07 — Объявить правдивый source artifact-cardinality fixture

**Scope:** tests/ddd/test_content_gates.py::test_artifact_cardinality_by_stage_uses_existing_path_only_interface.

**Исправление:** Объявить source соответственно фактическому появлению файлов: preexisting до входа либо самостоятельный producer/declared arrival с достоверными стадиями. Согласовать phase, gate refs, producer scope/exit и момент materialization. Не назвать файл preexisting, если он появляется только после проверяемого входа.

**Приёмка:** Один физический путь, предъявленный дважды, не удовлетворяет minimum=2; два разных существующих файла удовлетворяют диапазону. Несуществующий путь отвергается. Сохранить path-only интерфейс, не передавать agent-computed cardinality и не отключать source validation.

### DDD-P08 — Создавать историческую Task без двойного ownership

**Scope:** tests/ddd/test_content_gates.py::test_repository_rehydrates_historical_invalid_schedule_without_rewriting_it.

**Исправление:** Создать в disposable fixture unclaimed историческую Task или корректную отдельную identity, сохранив слой прежнего content contract. Не трогать реальные operational DB, не снимать unique index и не фабриковать SessionEnd.

**Приёмка:** Создание fixture достигает reload; точный task content layer и version неизменны. Исторический invalid schedule читается, новый такой schedule по-прежнему отклоняется. Проверить integrity/FK тестовой БД и отсутствие второй Task у одного claimant.

### DDD-P09 — Согласовать inspection fixture с сокращённым route

**Scope:** Дополнительный node tests/runtime_services/test_stage_contract_runtime.py::test_reviewer_revision_changes_gate_refs_atomically и его _configure(inspection=True); вне исходной выборки 48.

**Исправление:** После выбора двух стадий пересобрать decomposition.phases по фактическому process route; сохранить stage/role/scope контракты. Не ослаблять FocusedDecomposition.require_valid. Дальнейшие возможные prerequisites не объявлять исправленными заранее.

**Приёмка:** Node доходит до reviewer revision и проверяет атомарность старых/новых gate refs; негативная fixture с mismatched phases продолжает отвергаться до создания Task. Запускать адресные owning tests затронутого helper, а не полный suite.

## Связь с уже существующими maintenance Task

Сохранённые `0159–0162` не создаются повторно. `0159` исправляет другой backup-тест,
а не legacy Task application (DDD-P01). `0160` обслуживает две transfer fixtures,
`0161` — import batch/rework, `0162` — Evidence demo, **не Content demo** (DDD-P03).
В восстановленной очереди их repairs переданы на review; это не финальное принятие.
Кандидат `0162` — `2b7c782938a0f6e33c0ed6b717521de005d7846a`, отдельно от
исследованного `0163` worktree `ceac486`. Отсутствие этого исправления в старой ветке
не основание заново выполнять `0162` или удалять её данные.

[Реестр известных отказов](known-bugs.md#test-ddd-baseline--отказы-прежних-проверок-task-и-contentpolicy)
остаётся открытым до реальных адресных исправлений. Изменения данной Task ограничены
двумя документами; production, tests, Requirements Registry и соседние worktrees не
редактируются. Native SMOKE текущего этапа проверяет пригодность документального
кандидата к передаче, а не делает исходный baseline успешным. Независимый review,
финальное принятие Task, merge/push и развёртывание не следуют из этой классификации.
