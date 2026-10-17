# Работа с графом требований

Обновлено: **2026-10-17**.

Это реализованный профиль самостоятельного Requirements: System → Application →
Task с отношениями M:N на обоих переходах. Запуск, Python 3.13 и все параметры
конфигурации описаны в [инструкции CLI](shell-req.md). Формат данных и транзакции —
в [описании хранилища](storage-req.md). Сохраните выбранный ниже JSON-пакет в
`$requirements_work/request.json` и передайте его той же настроенной установке:

```sh
"$requirements_python" -B "$requirements_checkout/apps/requirements/bin/requirements" \
  --config "$requirements_work/config/requirements.json" \
  < "$requirements_work/request.json"
```

Используйте внешний пакет `operation/input`. В `query.input.queries` передавайте
непустой список с уникальными непустыми строками `id`; ответ сохраняет их порядок
и идентификаторы. Все элементы одного ответа относятся к одной общей `revision`.
Пакет возвращает полный результат либо описанный отказ. `batch.max_items` задаёт
предел элементов пакета и предел страницы поиска. Для `revise_task` этот же предел
действует на списки требований и Application IDs. `batch.max_input_bytes`
ограничивает весь входной пакет. Все пакеты примеров ниже используют
`batch.max_items:64`.

## Чтение и направления связей

| Намерение | `kind` и дополнительные поля запроса |
| --- | --- |
| System → Applications или Application → Systems | `chain`, `requirement_id` |
| Application → требования текущих постановок Task | `application_tasks`, `application_id` |
| Требование выбранной постановки Task → Applications | `task_chain`, `task_id`, `key`, `statement_revision` |
| Полная текущая постановка | `get_task`, `task_id` |
| Основания текущей постановки | `task_basis`, `task_id` |
| Первоначальное создание и выбранная форма | `task_creation`, `task_id` |
| Порядок постановок Task и их goals | `task_history`, `task_id` |
| Полные редакции System/Application | `history_requirement`, `requirement_id` |
| Сохранённый полный снимок | `snapshot`, `task_id`, `snapshot_revision` |
| Поиск System/Application | `search_requirements`, `filter`, `limit`, `after_id` |
| Полнота распределения ответственности | `coverage`, необязательный `mode` |
| Пробелы и типизированные placeholders | `gaps` |

`task_chain` возвращает `task` с `task_id`, `statement_revision`, `key`, `status`,
`text`, `applications` и список связанных Application records. Для каждого такого
Application используйте `chain`, чтобы прочитать его System-основания. Обратный
`application_tasks` возвращает Application record и подходящие Task records,
упорядоченные по `task_id/key`. Он выбирает текущую постановку каждой Task;
`task_chain` позволяет явно выбрать сохранённую редакцию. В текущих списках
родителей и обратных детей учитываются записи со статусом, отличным от `obsolete`.

`get_task` содержит `task_id`, `statement_revision`, `goal`, упорядоченные
`task_requirements`, `definition_of_done` и `snapshot_revision`. Последнее поле —
номер последнего сохранённого снимка этой Task либо `null`. Снимок может относиться
к более ранней постановке: для чтения его значений укажите собственную точную
`snapshot_revision`.

`task_history.items` содержит `statement_revision/goal` в порядке редакций.
`history_requirement.items` содержит `revision/status/text/owner_revision/origin`.
Снимок возвращается целиком как сохранённый JSON object; текущее изменение графа
сохраняет прежнее содержимое снимка.

## Применимость, пробелы и поиск

`coverage(mode="current")` учитывает содержательные текущие требования.
`coverage(mode="analysis")`, а также `coverage` без `mode`, учитывает содержательные
текущие и будущие требования. Статусы — `current`, `future`, `obsolete`.
Текст требования содержателен, когда после удаления пробелов остаются символы;
для явно будущего placeholder используйте `status:"future", text:""`.

Ответ `coverage` содержит три упорядоченных списка IDs:
`application_without_system`, `system_without_application`,
`application_without_task`. Будущее содержательное требование Task может покрыть
текущий Application в анализе и оставить его пробелом текущего режима. Пустые
placeholders остаются пробелами, а структурная связь описывает основание требования,
не факт его выполнения. `gaps` возвращает перечисленные пробелы и отдельные записи
`projected_placeholder` с уровнем `system`, `application` или `task`.

В `filter` поиска используйте выбранные поля `level` (`system/application`),
`status` и `text_contains`. Поиск подстроки чувствителен к регистру и поддерживает
Unicode. Пустой фильтр выбирает все записи. Задавайте целый `limit` от 1 до
`batch.max_items`; для первой страницы — `after_id:null`, для следующей —
возвращённый `next`. Результат содержит `rows` и `next`, равный последнему ID
страницы при наличии продолжения, иначе `null`. Порядок — по точному ID.
Каждый следующий пакет читает актуальную ревизию хранилища.

## Новое пустое хранилище

Первый запрос подготавливает самостоятельную схему. На новом хранилище следующий
пакет возвращает `status:"read_only"`, `revision:0`, три пустых списка в каждом
coverage, пустые gaps и поиск `rows:[], next:null`:

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {"id":"current","kind":"coverage","mode":"current"},
      {"id":"analysis","kind":"coverage","mode":"analysis"},
      {"id":"gaps","kind":"gaps"},
      {"id":"search","kind":"search_requirements","filter":{"level":"application","text_contains":"граф"},"limit":64,"after_id":null}
    ]
  }
}
```

Для этого примера выберите `batch.max_items:64`. Используйте
[пример начального заполнения System/Application](shell-req.md#создание-и-повторное-открытие)
для добавления собственных требований через `apply`.

## Создание Task и канонический ID

Начните эту последовательность в новом пустом хранилище, подготовив рабочую
конфигурацию по [инструкции CLI](shell-req.md). Используйте значения поставляемого
примера: `task_ids.namespace.minimum:1`, `maximum:999999`, `width:6`,
`progression.first:1`, `step:1`, `batch.max_items:64`. Каждый пакет ниже передавайте
командой из начала страницы. В этой последовательности исходная общая ревизия — 0;
разделы с подготовленными известными Task далее имеют свои отдельные исходные данные.

### 1. Зарегистрировать цель

`request_id` и содержательный `goal` достаточны для создания черновика. ID выбирает
Requirements по текущей явно заданной политике. Сохраните возвращённый `task_id` и
используйте именно эту строку при дальнейшей работе.

```json
{
  "operation": "create_task",
  "input": {
    "request_id": "report-draft",
    "goal": "Подготовить отчёт."
  }
}
```

Ответ содержит `status:"created"`, `task_id:"000001"`, `statement_revision:1`,
`revision:1`, `replayed:false`. Здесь `revision` — общая ревизия Requirements,
а `statement_revision` — ревизия постановки одной Task.

### 2. Прочитать текущую постановку, основания и исходное создание

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {"id":"current","kind":"get_task","task_id":"000001"},
      {"id":"basis","kind":"task_basis","task_id":"000001"},
      {"id":"creation","kind":"task_creation","task_id":"000001"}
    ]
  }
}
```

Ответ `status:"read_only"`, `revision:1` сохраняет порядок трёх результатов.
`get_task` возвращает goal, `task_requirements:[]`, `definition_of_done:[]`,
`statement_revision:1` и `snapshot_revision:null`. Отсутствующие при создании
материалы представлены пустыми списками текущего черновика.

`task_basis` возвращает `task_id`, `statement_revision`, `basis_ready:false` и
`requirements:[]`. `task_creation` возвращает `task_id`, исходный `request_id`,
полный `intent` и полный первоначальный `response`. В `intent` сохраняются именно
переданные поля: исходное отсутствие материалов и выбора формы остаётся отсутствием.

### 3. Добавить реальные основания

```json
{
  "operation": "apply",
  "input": {
    "request_id": "report-foundation",
    "expected_revision": 1,
    "operations": [
      {"kind":"put_requirement","requirement":{"id":"SYS-REPORT","level":"system","status":"current","text":"Пользователь получает отчёт."}},
      {"kind":"put_requirement","requirement":{"id":"APP-REPORT","level":"application","status":"current","text":"Приложение выдаёт отчёт."}},
      {"kind":"link","system":"SYS-REPORT","application":"APP-REPORT"}
    ]
  }
}
```

Успешный `apply` возвращает `status:"applied"`, `revision:2`, `replayed:false`.

### 4. Связать текущую постановку с основанием

```json
{
  "operation": "apply",
  "input": {
    "request_id": "report-statement",
    "expected_revision": 2,
    "operations": [
      {
        "kind": "revise_task",
        "task_id": "000001",
        "expected_statement_revision": 1,
        "statement": {
          "goal": "Подготовить отчёт.",
          "task_requirements": [
            {"key":"report","status":"current","text":"Выдать отчёт.","applications":["APP-REPORT"]}
          ],
          "definition_of_done": ["Отчёт доступен."]
        }
      }
    ]
  }
}
```

Ответ сообщает общую `revision:3`. Полная следующая постановка сохраняет ID и goal;
её `statement_revision` становится 2. Для каждого следующего изменения сначала
прочитайте актуальные общую ревизию, постановку и её содержимое.

### 5. Сопоставить текущую постановку с первоначальным созданием

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {"id":"current","kind":"get_task","task_id":"000001"},
      {"id":"basis","kind":"task_basis","task_id":"000001"},
      {"id":"creation","kind":"task_creation","task_id":"000001"}
    ]
  }
}
```

При общей `revision:3` текущая постановка имеет ревизию 2, указанное требование
и DoD. `task_basis` содержит `basis_ready:true` и единственный результат
`{"key":"report","basis_ready":true,"gaps":[]}`. Исходный `task_creation.response`
сохраняет `statement_revision:1`, `revision:1`, `replayed:false`; его `intent`
по-прежнему совпадает с первым пакетом. Историю постановок читает `task_history`.

### 6. Явно выбрать ID и форму работы

```json
{
  "operation": "create_task",
  "input": {
    "request_id": "period-draft",
    "task_id": "000002",
    "goal": "Выбрать период отчёта.",
    "work_choice": {"kind":"sequence","sequence_id":"REPORT-SEQUENCE"}
  }
}
```

Ответ: `status:"created"`, `task_id:"000002"`, `statement_revision:1`,
`revision:4`, `replayed:false`. Явный ID сохраняется как переданная непустая
строка и должен быть свободен в Requirements. Автоматическая числовая политика
применяется при отсутствии `task_id`; соседний потребитель использует возвращённый
канонический ID без преобразования строки.

Значение поля `work_choice` для самостоятельной работы — `{"kind":"standalone"}`;
для последовательности — объект из пакета выше с содержательным `sequence_id`.
Это явная декларация оператора или агента, сохранённая в первоначальном намерении.
Дальнейшее ведение членства принадлежит Sequencing, исполнение — Execution.
Обе формы создают только собственные данные Requirements.

### 7. Прочитать сохранённый выбор и повторить исходное создание

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {"id":"creation","kind":"task_creation","task_id":"000002"},
      {"id":"basis","kind":"task_basis","task_id":"000002"}
    ]
  }
}
```

При общей `revision:4` исходный `intent` второй Task содержит явно выбранные ID
и форму; её первоначальный ответ также имеет `revision:4`. Основания нового
черновика пока дают `basis_ready:false`, `requirements:[]`.

Теперь повторите **в точности первый пакет** этой последовательности. Ответ
содержит `000001`, `statement_revision:1`, `revision:1`, `replayed:true`.
Текущая постановка первой Task остаётся ревизией 2, общая ревизия — 4. Сохранённый
первоначальный ответ внутри `task_creation` сохраняет `replayed:false`.
Для повтора используйте прежний `request_id` и полное прежнее намерение;
для нового создания задавайте новый идентификатор запроса. Пространства запросов
`create_task` и `apply` независимы.

### Смысл оценки оснований

`task_basis` оценивает текущие сохранённые требования. Для каждого действующего
или будущего требования он возвращает `key`, `basis_ready` и `gaps` по существующему
правилу связей Application → System. Порядок требований сохраняется. Общее `true`
получается при наличии применимых требований и полной цепочке каждого из них.
Снятые требования (`obsolete`) исключаются; при отсутствии применимых требований
результат — `false` и пустой список. Пустые `future` placeholders и отсутствующие
верхние связи остаются пробелами. Активная ссылка на снятое Application-основание
даёт описанный отказ выбранного запроса; текущая постановка читается через `get_task`.

`basis_ready` описывает структурную полноту оснований. Готовность этапов, публикация
снимка и начало исполнения имеют собственных владельцев. Для записи материалов при
создании можно явно передать `task_requirements` и `definition_of_done` в тех же
формах, что в полной постановке шага 4; поля, которые вы передаёте, должны содержать
значения своих объявленных типов. Общая ревизия перед созданием Task не запрашивается.

## Подготовленная известная Task

Запросы Task принимают её существующий точный ID. Получите текущую постановку
через `get_task` и общую ревизию из ответа. Для изменения передайте актуальную
`expected_revision`, актуальную `expected_statement_revision`, полный следующий
statement и тот же `goal`. Сохраняйте ключи неизменённых требований; список
`applications` задаёт уникальные известные Application IDs. `definition_of_done`
содержит строки с содержательным текстом.

Следующие примеры используют подготовленную демонстрационную модель
[graph-state.json](../fixtures/graph-state.json): общая ревизия 7,
Task `000007` с постановкой 2 и снимком 1, Task `000008` с постановкой 1.
В ней уже существуют `SYS-A`, `SYS-B`, `APP-A`, `APP-B` и остальные записи fixture.
Это исходные данные проверки примеров. Новая установка начинает с пустой модели;
новая Task создаётся по [инструкции создания](#создание-task-и-канонический-id).
Публикация новых снимков остаётся отдельной будущей возможностью.

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {"id":"task","kind":"get_task","task_id":"000008"},
      {"id":"parents","kind":"task_chain","task_id":"000007","key":"R2","statement_revision":2},
      {"id":"children","kind":"application_tasks","application_id":"APP-A"},
      {"id":"history","kind":"history_requirement","requirement_id":"SYS-A"},
      {"id":"statements","kind":"task_history","task_id":"000008"},
      {"id":"snapshot","kind":"snapshot","task_id":"000007","snapshot_revision":1},
      {"id":"search","kind":"search_requirements","filter":{"level":"application","status":"current","text_contains":"граф"},"limit":1,"after_id":null}
    ]
  }
}
```

Ответ имеет ревизию 7. `parents` содержит `APP-A/APP-B`; `children` —
`000007/R1`, `000007/R2`, `000008/R1`. Поиск возвращает `APP-A`, `next:"APP-A"`.

### Добавление второго основания

`revise_task` создаёт следующую полную постановку. Хранилище сохраняет прежние
ключи, тексты, связи и порядок. Публичный `get_task` возвращает текущую полную
постановку, `task_chain` — выбранный исторический key с его текстом и основаниями,
`task_history` — номера постановок и goals. Полный опубликованный вариант читается
через существующий `snapshot`. У демонстрационной Task `000008` снимков нет;
её прежний полный упорядоченный список сохраняется в хранилище, а публичное
историческое чтение показывает выбранные keys и заголовки. Для нового изменения
используйте новый `request_id`.

```json
{
  "operation": "apply",
  "input": {
    "request_id": "doc-add-parent",
    "expected_revision": 7,
    "operations": [
      {
        "kind": "revise_task",
        "task_id": "000008",
        "expected_statement_revision": 1,
        "statement": {
          "goal": "Использовать общее требование.",
          "task_requirements": [
            {"key":"R1","status":"current","text":"Использовать общее основание.","applications":["APP-A","APP-B"]}
          ],
          "definition_of_done": ["Оба основания сохранены."]
        }
      }
    ]
  }
}
```

Результат — `status:"applied", revision:8, replayed:false`; постановка Task
получает номер 2. Точный повтор исходного пакета возвращает его сохранённую
ревизию 8 с `replayed:true`. Порядок ключей JSON object при сравнении намерения
свободен; порядок списков и типы значений сохраняются.

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {"id":"parents","kind":"task_chain","task_id":"000008","key":"R1","statement_revision":2},
      {"id":"children","kind":"application_tasks","application_id":"APP-B"},
      {"id":"history","kind":"task_history","task_id":"000008"},
      {"id":"snapshot","kind":"snapshot","task_id":"000007","snapshot_revision":1}
    ]
  }
}
```

Теперь `parents` содержит `APP-A/APP-B`, обратное чтение — `000007/R2` и
`000008/R1`, история Task — редакции 1 и 2. Старый снимок `000007/1` сохраняется.

### Завершение периода верхней связи

```json
{
  "operation": "apply",
  "input": {
    "request_id": "doc-unlink",
    "expected_revision": 8,
    "operations": [{"kind":"unlink","system":"SYS-B","application":"APP-A"}]
  }
}
```

Ревизия становится 9; период выбранной связи закрывается. Текущий `APP-A`
имеет System-основание `SYS-A`; сохранённый снимок продолжает описывать прежний граф.

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {"id":"application","kind":"chain","requirement_id":"APP-A"},
      {"id":"parents","kind":"task_chain","task_id":"000007","key":"R2","statement_revision":2},
      {"id":"snapshot","kind":"snapshot","task_id":"000007","snapshot_revision":1}
    ]
  }
}
```

### Явное нижнее обнаружение

Создайте новые System/Application records через `put_requirement`, свяжите их
операцией `link` и включите новый Application в ту же известную Task через
`revise_task`. Порядок операций задаёт доступность создаваемых оснований.
Весь пакет сохраняется одной транзакцией и получает одну общую ревизию.

```json
{
  "operation": "apply",
  "input": {
    "request_id": "doc-discovery",
    "expected_revision": 9,
    "operations": [
      {"kind":"put_requirement","requirement":{"id":"SYS-DOC","level":"system","status":"current","text":"Сохранять структуру требований."}},
      {"kind":"put_requirement","requirement":{"id":"APP-DOC","level":"application","status":"current","text":"Просматривать основания задачи."}},
      {"kind":"link","system":"SYS-DOC","application":"APP-DOC"},
      {
        "kind": "revise_task",
        "task_id": "000008",
        "expected_statement_revision": 2,
        "statement": {
          "goal": "Использовать общее требование.",
          "task_requirements": [
            {"key":"R1","status":"current","text":"Использовать общее основание.","applications":["APP-A","APP-B","APP-DOC"]}
          ],
          "definition_of_done": ["Три основания сохранены."]
        }
      }
    ]
  }
}
```

Результат имеет ревизию 10. Task сохраняет ID и goal; её постановка получает
номер 3. Чтение подтверждает обе стороны нового пути и сохранённую историю:

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {"id":"task","kind":"get_task","task_id":"000008"},
      {"id":"parents","kind":"task_chain","task_id":"000008","key":"R1","statement_revision":3},
      {"id":"application","kind":"chain","requirement_id":"APP-DOC"},
      {"id":"history","kind":"task_history","task_id":"000008"},
      {"id":"requirement-history","kind":"history_requirement","requirement_id":"APP-DOC"}
    ]
  }
}
```

## Обслуживание и новый системный смысл

Для обслуживания используйте Application-основание с осмысленным описанием
исправления, связанное с действующим System-требованием о реальной возможности.
Свяжите требование известной Task с этим основанием через `revise_task`.
Если работа выявила новую возможность системы, явно добавьте отдельные System
и Application требования и их связь; прежнее основание обслуживания сохраните.

Перед изменением прочитайте текущую постановку через `get_task`, её основания
через `chain` и общую ревизию из ответа `query`. В полной следующей постановке
сохраните действующие goal, DoD и остальные требования, добавляя нужную запись.

В примерах ниже исходный реестр имеет общую `revision:1` и содержит только
действующее System-требование `SYS-EXPORT` с текстом «Пользователь получает отчёт
с сохранёнными значениями.». Известная Task `000041` имеет `statement_revision:1`,
goal «Восстановить отчёт.», пустые `task_requirements` и `definition_of_done`.
Это явно подготовленный вход, аналогичный [известной Task](#подготовленная-известная-task),
а не инструкция создания новой Task в пустом приложении. Самостоятельный профиль
работает с такими известными данными; для новой Task используйте
[создание черновика](#создание-task-и-канонический-id). Публикация развивается отдельно.
В своей установке используйте прочитанные IDs, ревизии и полное содержимое постановки.

### Основание обслуживания

Передайте следующий пакет настроенному CLI:

```json
{
  "operation": "apply",
  "input": {
    "request_id": "maintenance",
    "expected_revision": 1,
    "operations": [
      {
        "kind": "put_requirement",
        "requirement": {
          "id": "APP-EXPORT-MAINTENANCE",
          "level": "application",
          "status": "current",
          "text": "Основание обслуживания: восстановить сохранённые значения отчёта."
        }
      },
      {"kind":"link","system":"SYS-EXPORT","application":"APP-EXPORT-MAINTENANCE"},
      {
        "kind": "revise_task",
        "task_id": "000041",
        "expected_statement_revision": 1,
        "statement": {
          "goal": "Восстановить отчёт.",
          "definition_of_done": [],
          "task_requirements": [
            {
              "key": "repair",
              "status": "current",
              "text": "Восстановить значения отчёта.",
              "applications": ["APP-EXPORT-MAINTENANCE"]
            }
          ]
        }
      }
    ]
  }
}
```

Ответ содержит `status:"applied", revision:2, replayed:false`; текущая постановка
Task получает `statement_revision:2`. Реестр сохраняет одно исходное System-требование.
Назначение основания объясняется его текстом, а системная цепочка описывает
существующую возможность. Специальная структурированная классификация auxiliary
остаётся [целевым проектом](req-model.md#4-auxiliary--назначение-основания-не-четвёртый-уровень).

### Добавление нового системного смысла

Продолжите после предыдущего пакета. Явно создайте новую пару требований и добавьте
требование `period` в следующую постановку, сохранив прежнее `repair`:

```json
{
  "operation": "apply",
  "input": {
    "request_id": "new-meaning",
    "expected_revision": 2,
    "operations": [
      {
        "kind": "put_requirement",
        "requirement": {
          "id": "SYS-EXPORT-FILTER",
          "level": "system",
          "status": "current",
          "text": "Пользователь выбирает период отчёта."
        }
      },
      {
        "kind": "put_requirement",
        "requirement": {
          "id": "APP-EXPORT-FILTER",
          "level": "application",
          "status": "current",
          "text": "Приложение формирует отчёт за выбранный период."
        }
      },
      {"kind":"link","system":"SYS-EXPORT-FILTER","application":"APP-EXPORT-FILTER"},
      {
        "kind": "revise_task",
        "task_id": "000041",
        "expected_statement_revision": 2,
        "statement": {
          "goal": "Восстановить отчёт.",
          "definition_of_done": [],
          "task_requirements": [
            {
              "key": "repair",
              "status": "current",
              "text": "Восстановить значения отчёта.",
              "applications": ["APP-EXPORT-MAINTENANCE"]
            },
            {
              "key": "period",
              "status": "current",
              "text": "Добавить выбор периода отчёта.",
              "applications": ["APP-EXPORT-FILTER"]
            }
          ]
        }
      }
    ]
  }
}
```

Ответ содержит `status:"applied", revision:3, replayed:false`; постановка Task
получает `statement_revision:3`. У прежнего `APP-EXPORT-MAINTENANCE` сохраняются
identity, текст, локальная ревизия `1`, связь с `SYS-EXPORT` и ссылка требования
`repair`. Новая пара имеет собственные IDs и отдельную связь с требованием `period`.
Прочитайте сохранённый результат новым запуском CLI:

```json
{
  "operation": "query",
  "input": {
    "queries": [
      {"id":"task","kind":"get_task","task_id":"000041"},
      {"id":"maintenance","kind":"chain","requirement_id":"APP-EXPORT-MAINTENANCE"},
      {"id":"new-meaning","kind":"chain","requirement_id":"APP-EXPORT-FILTER"},
      {"id":"maintenance-history","kind":"history_requirement","requirement_id":"APP-EXPORT-MAINTENANCE"}
    ]
  }
}
```

Два этих сценария проверяются через самостоятельный CLI и реальную базу в
`apps/requirements/tests/test_maintenance.py`. Существующее основание и новая
возможность остаются отдельными требованиями с сохранёнными связями.

## Границы и результаты операций

Изменение System/Application через `put_requirement` сохраняет следующую локальную
редакцию record; для снятия с применения задайте `status:"obsolete"`. Верхние связи
изменяются `link/unlink`, основания требований Task — полной следующей постановкой.
История и значения снимков читаются отдельно от текущей применимости.

Текущий профиль предоставляет `create_task/apply/query/import_bootstrap`: создание
Task и ID, описанные чтения и изменения постановки. Публикация snapshot,
purpose-aware готовность, outcomes и satisfaction относятся к последующим
возможностям [целевого контракта](contract-req.md).

Описанный отказ CLI возвращает exit 2 и JSON object с `status:"rejected"` и
содержательным `reason`. Публичный Python API использует `PoiseError` для описанных
отказов выбранной операции. Задавайте строковые виды запросов, известные IDs,
уникальные keys/Application sets и действующие целые номера ревизий. Изменения
сохраняются атомарно. Чтение сохранённого JSON выполняется для выбранного значения;
его нечитаемость описывается как ошибка этой операции, а остальные читаемые данные
остаются доступны. Восстановление данных выполняет оператор отдельным инструментом.

Рабочая конфигурация и состояние принадлежат выбранной установке. Приложение
хранит полное намерение и значения; при запуске используются необходимая metadata
схемы и данные текущей операции. Контрольные суммы и проверки подмены файлов
условием запуска и чтения этого профиля не являются.
