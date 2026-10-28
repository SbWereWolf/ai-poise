# Контракт файловой политики Poise

## Назначение и состояние

Контракт задаёт ограничения чтения и записи внутри настроенных файловых roots.
Выбраны один владелец решения, проверка собственным файловым адаптером до
целевого эффекта, совместное применение совпавших roots и принадлежность по
фактическому назначению пути.

Самостоятельный Poise исполняет `paths.evaluate`, `files.write` и выбранное
подключение политики к `reader.read/next` через существующий `bin/poise`.
Достаточно CPython 3.13 со стандартной библиотекой и явной конфигурации.
Прямые расчёт и запись работают до подготовки Session; reader использует
[свою Session и состояние](poi-file-contract.md#владелец-и-поддержанное-использование).

Эта страница — действующий договор файловых операций. [Оболочка](shell-poi.md)
владеет общим запуском, строгим JSON, лимитом пакета и внешним `stride-poise/v1`.
Более широкий [проект Poise](contract-poi.md) сохраняет будущие generic gateway,
учёт запросов и асинхронные команды; они не требуются для этих операций.

## Владельцы и точка enforcement

1. Оператор задаёт версию политики и roots в `paths` конфигурации Poise.
2. Предметный адаптер разбирает публичные аргументы выбранного файлового инструмента
   и перечисляет **все** его запрошенные target paths и операции.
3. Общий владелец политики разрешает roots и target paths, затем вычисляет решение
   для всего набора. Он наблюдает metadata пути; содержимое файлов не читает и
   файлы не изменяет.
4. Адаптер проверяет результат и выполняет допущенную операцию с теми же
   подготовленными абсолютными путями. При отказе целевые операции не начинаются.
5. Собственный провайдер возвращает полный результат в действующем внешнем
   ответе Poise; вызывающий сохраняет нужный ему ответ.

Общий [FilePolicy](../application/filesystem.py) соединяет подготовку путей
[LocalFilesystem](../infrastructure/filesystem.py) с чистым решением
[доменного владельца](../domain/filesystem.py). Файловая политика не читает
содержимое целевых файлов. Действующий [ReaderOperations](../application/reader.py)
вызывает её один раз для всего пакета перед первым `ReaderFiles.read` и использует
полученные назначения. `write_files` проверяет весь набор и виды целей перед
первым фактическим `write`. [Shell](../shell.py) соединяет эти операции;
прямые расчёт и запись выбираются перед созданием владельцев Session и caller.

Адаптер получает перечень эффектов из валидированных аргументов своего инструмента.
Он не принимает неподтверждённый перечень от клиента вместо этих аргументов.
Parser, подготовка путей и исполнитель относятся к одной версии binding.
Существующий bounded reader сохраняет свои диапазоны, ACK, поколения и квитанции;
политика roots не копирует его реализацию.

Root policy дополняет public arguments, лимиты и применимые проверки выбранного
провайдера. Прямые расчёт и запись используют описанный здесь договор без Session
или роли; reader сохраняет свои проверки состояния. Native tools и произвольные
программы без этого подключения не объявляются перехваченными. По `cwd` команды нельзя вывести все её
файловые эффекты. Внутреннее Session/result storage остаётся у своего владельца;
эта граница контролирует объявленные целевые файловые операции адаптера.

## Конфигурация `poise-fs-policy/v1`

Значение `paths` содержит следующие обязательные известные поля:

| Поле | Тип и смысл |
| --- | --- |
| `schema` | Строка, ровно `poise-fs-policy/v1`. |
| `roots` | Массив объектов root; пустой массив явно задаёт отсутствие ограничиваемых областей. |
| `roots[].id` | Непустой UTF-8 текст без NUL, уникальный в конфигурации; значение не обрезается и не переименовывается. |
| `roots[].path` | Непустой абсолютный native path без NUL; после обычного разрешения это существующий каталог. |
| `roots[].read` | JSON boolean; разрешение чтения в области root. |
| `roots[].write` | JSON boolean; разрешение записи в области root. |

Все перечисленные поля обязательны; числа не подменяют boolean, строки не
преобразуются в другие типы. Неизвестные опции конфигурации игнорируются при
вычислении, их исходные значения остаются в рабочем файле. Загрузка и вызовы приложения
сохраняют байты конфигурации без перезаписи; отдельный редактор здесь не вводится.

Два root с одинаковым разрешённым каталогом означают ошибку конфигурации, в том
числе при разных ID или разных ссылочных именах этого каталога. Вложенные разные
каталоги допустимы. Правило совместного применения является семантикой выбранной
версии; приоритет по порядку списка и скрытые режимы отсутствуют.
Изменение обязательных полей или смысла решения получает новую версию;
конфигурация и provider binding выбирают её явно.

Корректный пример для заранее подготовленных каталогов:

```json
{
  "schema": "poise-fs-policy/v1",
  "roots": [
    {
      "id": "workspace",
      "path": "/srv/poise/workspace",
      "read": true,
      "write": true
    },
    {
      "id": "reference",
      "path": "/srv/poise/workspace/reference",
      "read": true,
      "write": false
    }
  ]
}
```

Здесь `workspace` допускает обе операции, а вложенный `reference` сохраняет
чтение и ограничивает запись. Оператор выбирает существующие каталоги и явно
задаёт оба разрешения каждого root до вызова адаптера.

## Вход владельца: `poise-fs-request/v1`

Адаптер передаёт отдельно явную конфигурацию политики и объект запроса. Объект
запроса содержит ровно следующие поля:

| Поле | Тип и смысл |
| --- | --- |
| `schema` | Строка, ровно `poise-fs-request/v1`. |
| `base` | Непустой абсолютный native path без NUL; существующий каталог, база относительных target paths. |
| `effects` | Непустой массив объектов эффектов в порядке входного запроса. |
| `effects[].operation` | Ровно `read` или `write`. |
| `effects[].path` | Непустой native path без NUL; абсолютный либо относительный к явному `base`. |

Объект эффекта содержит ровно `operation` и `path`. Дополнительные поля в запросе
и эффектах отклоняются. Отсутствующее значение отличается от `null`: обязательное
поле с `null` не удовлетворяет указанному типу. База не берётся из окружения
или текущего каталога процесса автоматически. Размеры packet и поддержанного
provider input остаются у существующих явных лимитов Poise/провайдера; этот
контракт не вводит скрытого числового предела или таймаута.

Разрешение выполняется обычной файловой семантикой поддержанного host:

- `read`: фактическое назначение существует;
- `write`: назначение существует либо отсутствует только конечный leaf при
  существующем каталоге назначения-родителя;
- относительный путь разрешается от `base`, ссылки — до фактического назначения;
- принадлежность сравнивается по компонентам разрешённого пути, включая сам root.

Тип данных и допустимый вид target определяет сам файловый провайдер. Политика
не превращает каталог в допустимый текстовый файл и не заменяет его проверки.
Предварительное разрешение не создаёт родителей или target и не исправляет
конфигурацию. Неудачное разрешение имеет явный ошибочный исход.

Корректный запрос чтения существующего файла для приведённой конфигурации:

```json
{
  "schema": "poise-fs-request/v1",
  "base": "/srv/poise/workspace",
  "effects": [
    {
      "operation": "read",
      "path": "guide.txt"
    }
  ]
}
```

Это также точный `input` действующей операции `paths.evaluate`. Для записи
`files.write` и чтения reader адаптер сам составляет этот запрос из их публичных
аргументов. Отдельный generic `tool.invoke` для такого вызова не требуется.

## Решение и результат `poise-fs-result/v1`

Сначала проверяются конфигурация и весь запрос, разрешаются все необходимые
пути. Затем для каждого target определяется набор содержащих его roots.

- `outside`: набор пуст; эта policy не вводит ограничения.
- `allow`: набор непуст, нужное разрешение равно `true` у каждого root.
- `deny`: набор непуст, нужное разрешение равно `false` хотя бы у одного root.

`read` использует `read`, `write` использует `write`. Вложенный root добавляет
своё ограничение и не отменяет ограничение родителя. Порядок roots не влияет на
решение. Для batch итог равен `deny`, если есть хотя бы один `deny`; иначе
`outside`, если все эффекты `outside`; иначе `allow`.

Полный результат имеет ровно следующие поля:

| Поле | Тип и связь |
| --- | --- |
| `schema` | Ровно `poise-fs-result/v1`. |
| `status` | Ровно `evaluated` либо `rejected`. |
| `decision` | При `evaluated`: `allow`, `deny` или `outside`; при `rejected`: `null`. |
| `effects` | При `evaluated`: по одной записи на каждый входной эффект; при `rejected`: `[]`. |
| `error` | При `evaluated`: `null`; при `rejected`: объект ошибки. |

Каждая запись `effects` содержит ровно:

| Поле | Тип и связь |
| --- | --- |
| `index` | JSON integer от 0; равен позиции входного эффекта. |
| `operation` | Тот же `read` или `write`, что во входном эффекте. |
| `path` | Подготовленное абсолютное фактическое назначение. |
| `roots` | Уникальные ID всех содержащих target roots, отсортированные по Unicode code points, без locale/case преобразования. |
| `decision` | `allow`, `deny` или `outside` по правилу выше. |

Записи эффектов сохраняют входной порядок. `roots:[]` соответствует `outside`;
непустой `roots` — `allow` или `deny`. Успешный расчёт даёт `error:null`, включая
политический отказ `deny`; это определённое решение, а не ошибка разбора.

Полный результат правильного запроса чтения:

```json
{
  "schema": "poise-fs-result/v1",
  "status": "evaluated",
  "decision": "allow",
  "effects": [
    {
      "index": 0,
      "operation": "read",
      "path": "/srv/poise/workspace/guide.txt",
      "roots": ["workspace"],
      "decision": "allow"
    }
  ],
  "error": null
}
```

`allow` означает согласие только этой policy. `outside` также оставляет решение
другим владельцам разрешений. Адаптер начинает целевые эффекты только когда
остальные permissions допускают вызов и итог policy равен `allow` либо `outside`.
При `deny` или `rejected` ни один запрошенный target effect этого вызова не
начинается. Диагностика собственного запроса может сохраняться его существующим
владельцем; она не подменяется целевой файловой операцией.

## Ошибки

Объект ошибки содержит ровно `code`, `field`, `effect_index`:

| Поле | Тип |
| --- | --- |
| `code` | Один из кодов ниже. |
| `field` | JSON pointer в логическом входе `/policy` либо `/request`; непустая строка. |
| `effect_index` | Индекс соответствующего эффекта либо `null` для конфигурации/общего запроса. |

| Код | Условие |
| --- | --- |
| `unsupported_schema` | Значение строковой версии политики или запроса не поддерживается. |
| `invalid_policy` | Структура/тип обязательной настройки, уникальность ID или разрешённых каталогов, вид root нарушает договор. |
| `invalid_request` | Структура/тип/операция/дополнительное поле запроса либо вид `base` нарушает договор. |
| `path_unresolvable` | Обычное разрешение root/base/target не удалось, read target отсутствует или write parent не существует. |

Конфигурация проверяется прежде запроса. Внутри корректной формы пути эффектов
обрабатываются во входном порядке; при ошибке возвращается `rejected`, пустой
`effects` и ссылка на обнаруженную ошибку. Первая ошибка разрешения имеет индекс
своего эффекта. При нескольких структурных ошибках достаточно указать одну
действительную ошибку своей категории: контракт не обещает полный набор findings.
Корректная форма версии с другим значением даёт `unsupported_schema`, отсутствующая
или неверно типизированная версия — `invalid_policy`/`invalid_request`.

Адаптер различает отказ policy, ошибочный вход и ошибку уже допущенного провайдера.
Внешний ответ сохраняет полный применимый результат политики или провайдера.
Действующие схемы и точные внешние ошибки определены ниже; будущий generic
учёт запросов T016 не объявляется уже реализованным.
Передача правильного JSON или успешный transport сами по себе не означают
business success. При ошибке I/O после допуска применяется договор провайдера;
автоматический rollback или повтор внешнего эффекта этой policy не обещан.

## Выбор политики и самостоятельный запуск

Используйте [полный пример конфигурации](../config/filesystem.example.json).
Он содержит обязательные параметры [общей конфигурации](shell-poi.md#явная-конфигурация),
явные [лимиты reader](poi-file-contract.md#конфигурация-и-команда) и `paths` выше.
Рабочую копию заполните своими абсолютными существующими roots. `base` операции
также абсолютный существующий каталог; относительные цели относятся к нему,
а `session_store` и `caller.binding_file` — к каталогу рабочего конфига.

| Операция | Выбор `paths` и зависимость от состояния |
| --- | --- |
| `paths.evaluate`, `files.write` | Раздел `paths` обязателен; собственная Session, caller binding и SQLite для выполнения не требуются. Общая конфигурация остаётся обязательной. |
| `reader.read`, `reader.next` | Присутствие ключа `paths` включает политику; каждый его известный параметр проверяется. При отсутствии ключа действует прежний reader. Нужны подготовленное состояние, Session и reader-контекст. |
| `reader.context/reset`, Session и hooks | Используют собственные действующие договоры; `paths` не разбирают. |

Явные `roots: []` включают корректную нейтральную политику: итог всех эффектов
`outside`. Присутствующее значение `paths` проверяется как конфигурация, а не
заменяется настройками по умолчанию. Общие строгий JSON/конфигурация/внешняя схема
и операция проверяются первыми; затем выбранные `paths` и все roots, после этого
аргументы провайдера и общий запрос. Ошибки policy имеют логические указатели
`/policy/...` и `/request/...`.

Запуск из корня checkout с явно выбранным `POISE_PYTHON`:

```bash
"$POISE_PYTHON" -I -S -B apps/poise/bin/poise --config /srv/poise/config/poise.json
```

Передайте один пакет UTF-8 и EOF на stdin. Внешний пакет содержит ровно `schema`,
`operation`, `input`; ответ — ровно `schema`, `status`, `result`, `error`, `evidence`.
Внешняя схема равна `stride-poise/v1`, `evidence` равен `[]`; `ok` соответствует
exit `0`, `unavailable` — `1`, `rejected` — `2`. Ответ завершается LF, stderr пуст.

## Прямой расчёт `paths.evaluate`

`input` равен полному запросу `poise-fs-request/v1` выше. Успешный расчёт возвращает
`status: "ok"`, `error: null` и полный `poise-fs-result/v1` в `result`.
В том числе рассчитанное `decision: "deny"` — успешный расчёт с exit `0`.
Эта операция наблюдает метаданные и возвращает решение; целевого чтения или записи
содержимого она не выполняет. Выполняющий адаптер получает собственный расчёт
для действительных аргументов своего вызова.

## Запись `files.write`

Точный `input` содержит следующие поля; каждый элемент `writes` — ровно `path`, `text`:

| Поле | Действующий смысл |
| --- | --- |
| `schema` | Ровно `poise-write-request/v1`. |
| `base` | Обязательный ключ; его переданное значение проверяет общий владелец как абсолютный существующий каталог. |
| `writes` | Непустой массив записей в порядке выполнения. |
| `writes[].path` | Непустой native path без NUL, абсолютный или относительно `base`. |
| `writes[].text` | JSON-строка, кодируемая в UTF-8; пустая строка допустима. Записываются её точные байты, включая переданные переводы строк. |

Провайдер проверяет точную форму, наличие `base`, свою схему, список и поля записей;
все значения присутствующего `base` передаются без преобразования общему владельцу.
Все тексты кодируются, все пути подготавливаются, решение для всего набора получается
и виды целей проверяются перед первым write. Допустим существующий обычный файл
либо новый конечный leaf с существующим родителем. Запись заменяет всё содержимое
существующего файла или создаёт новый файл. Повторяющиеся назначения — допустимые
последовательные записи; окончательные байты принадлежат последней успешной записи.
Родительские каталоги оператор готовит заранее.

Корректный пакет для подготовленного workspace:

```json
{
  "schema": "stride-poise/v1",
  "operation": "files.write",
  "input": {
    "schema": "poise-write-request/v1",
    "base": "/srv/poise/workspace",
    "writes": [{"path": "new.txt", "text": "Ж\n"}]
  }
}
```

Его полный успешный ответ:

```json
{
  "schema": "stride-poise/v1",
  "status": "ok",
  "result": {
    "schema": "poise-write-result/v1",
    "status": "written",
    "policy": {
      "schema": "poise-fs-result/v1",
      "status": "evaluated",
      "decision": "allow",
      "effects": [{"index": 0, "operation": "write", "path": "/srv/poise/workspace/new.txt", "roots": ["workspace"], "decision": "allow"}],
      "error": null
    },
    "items": [{"index": 0, "path": "/srv/poise/workspace/new.txt", "bytes_written": 3}]
  },
  "error": null,
  "evidence": []
}
```

`index` соответствует входной позиции, `path` — тому же подготовленному назначению,
`bytes_written` — полному числу записанных UTF-8 байтов.

## Подключение к reader

При выбранных `paths` адаптер переводит все валидированные элементы `read/next`
в эффекты `read`. Для `read` база равна явному `cwd`; для `next` используются
абсолютные пути действительных курсоров, а база явно равна родителю первого пути.
Текущий cwd процесса не подменяет эти значения. Решение для всего запроса и
подготовка всех назначений предшествуют первому чтению содержимого.

Собственная Session, поколение, ACK и диапазоны остаются у reader. При допуске
возвращается прежний полный [reader/2](poi-file-contract.md) без нового поля policy.
Отказ policy возвращает полный общий результат по следующему разделу; весь пакет
сохраняет прежние квитанции и ACK. `reader.context/reset` сохраняют свой договор.

## Внешние ошибки и продолжение

В таблице `inner` — полный `poise-fs-result/v1`, `inner.error` содержит ровно
`code`, `field`, `effect_index`. Во всех ответах `evidence: []`.

| `error.code` | Статус / exit; `result`; точные `details`; `action` | Правильное действие |
| --- | --- | --- |
| `paths_not_configured` | unavailable / 1; `null`; `{}`; `configure:paths` | Заполнить рабочий `paths` по этому договору. |
| `path_policy_rejected` | rejected / 2; полный `inner` со статусом rejected; `inner.error`; `correct:paths` | По указателю заполнить корректную конфигурацию или запрос и подготовить существующие каталоги. |
| `path_denied` | rejected / 2; полный evaluated/deny `inner`; `{}`; `configure:paths` | Выбрать предусмотренную оператором область с нужным разрешением. |
| `invalid_request` | rejected / 2; `null`; `{path}`; `correct:request` | Передать точные поля writer; указатель относится к `/input` или его полю. |
| `unsupported_schema` writer | rejected / 2; `null`; `{schema}`; `select:schema` | Выбрать `poise-write-request/v1`. |
| `io_unavailable` | unavailable / 1; `null`; `{reason}` с наблюдённым текстом OSError; `configure:files` | Настроить доступ к выбранным файлам и каталогам. |
| `file_write_failed` | unavailable / 1; частичный `poise-write-result/v1`; `{index,path,errno}`; `inspect:files` | Осмотреть фактические цели и завершённые элементы, затем выбрать необходимую явную операцию. |

При отсутствии ключа writer `base` это provider `invalid_request` с
`details.path: "/input/base"`. Проверка любого присутствующего значения принадлежит
общему запросу: неверный тип, текст, относительная база или вид существующего объекта
дают inner `invalid_request`, недоступное назначение — `path_unresolvable`.
В обоих случаях `field: "/request/base"`, `effect_index: null`, внешняя ошибка
`path_policy_rejected`, `action: "correct:paths"` и полный rejected `result`.
Остальные provider shape/type ошибки сохраняют указатели `/input/...`; неподдержанная
строковая writer schema даёт внешний `unsupported_schema`, а тип схемы проверяется
как форма запроса. Общие строгий JSON, внешняя schema, конфигурация и лимит пакета
сохраняют ошибки [оболочки](shell-poi.md#диагностируемые-отказы).

При обычной ошибке записи `result` содержит ровно `schema: "poise-write-result/v1"`,
`status: "failed"`, полный допущенный `policy` и `items` только ранее полностью
завершённых записей. В `details` находятся фактические индекс, подготовленный путь
и целое `errno` либо `null`. Неудавшийся файл также мог получить изменения;
при осмотре учитывайте все фактические цели. Ранее выполненные записи сохраняются.
Оператор определяет нужное продолжение после осмотра; автоматическое повторение,
откат пакета и исправление конфигурации этим интерфейсом не обещаны.

## Независимые проверочные данные

Это ожидаемые решения проверки политики **до** выполнения целевой операции.
Они задаются независимо от production evaluator. В реальной квалификации пути
размещаются в отдельной тестовой файловой области с заранее заданными объектами.

Конфигурация проверки содержит существующие каталоги:

| ID | Каталог | read | write |
| --- | --- | --- | --- |
| `project` | `/fixture/project` | `true` | `true` |
| `reference` | `/fixture/project/reference` | `true` | `false` |
| `private` | `/fixture/project/private` | `false` | `false` |
| `archive` | `/fixture/archive` | `true` | `false` |

`base=/fixture/project`. Существуют `a.txt`, `reference/ref.txt`,
`private/p.txt`, `/fixture/archive/ref.txt`, `/fixture/outside/a.txt` и
`/fixture/project2/a.txt`. Конечный leaf `new.txt` отсутствует; его родитель существует.
Обычные ссылки заранее имеют назначения:
`archive-link` → `/fixture/archive/ref.txt`,
`outside-link` → `/fixture/outside/a.txt`,
`reference-link` → `/fixture/project/reference/ref.txt`.

| № | Операция и входной путь | Фактический путь / roots | Ожидаемый исход |
| --- | --- | --- | --- |
| F1 | `read a.txt` | `/fixture/project/a.txt`, `[project]` | `evaluated/allow` |
| F2 | `write a.txt` | `/fixture/project/a.txt`, `[project]` | `evaluated/allow` |
| F3 | `read reference/ref.txt` | `/fixture/project/reference/ref.txt`, `[project, reference]` | `evaluated/allow` |
| F4 | `write reference/ref.txt` | тот же путь, `[project, reference]` | `evaluated/deny` |
| F5 | `read private/p.txt` | `/fixture/project/private/p.txt`, `[private, project]` | `evaluated/deny` |
| F6 | `read /fixture/outside/a.txt` | тот же путь, `[]` | `evaluated/outside` |
| F7 | `read /fixture/project2/a.txt` | тот же путь, `[]` | `evaluated/outside`; строковый префикс не является принадлежностью |
| F8 | `write new.txt` | `/fixture/project/new.txt`, `[project]` | `evaluated/allow` |
| F9 | `read archive-link` | `/fixture/archive/ref.txt`, `[archive]` | `evaluated/allow` |
| F10 | `write archive-link` | тот же путь, `[archive]` | `evaluated/deny` |
| F11 | `write outside-link` | `/fixture/outside/a.txt`, `[]` | `evaluated/outside` этой policy |
| F12 | `write reference-link` | `/fixture/project/reference/ref.txt`, `[project, reference]` | `evaluated/deny` |
| F13 | batch: `read a.txt`, `write reference/ref.txt` | индексы 0/1, решения `allow`/`deny` | итог `deny`, оба запрошенных эффекта не начинаются |

Дополнительные независимые условия:

| № | Условие | Ожидаемый результат |
| --- | --- | --- |
| F14 | Полный запрос F1, `roots:[]` | `evaluated/outside`, `roots:[]` |
| F15 | Другой порядок четырёх roots, запрос F4 | тот же результат и порядок ID `[project, reference]` |
| F16 | Неизвестная configuration option добавлена к policy F1 | результат F1, исходное значение и байты конфигурации сохранены |

Это данные конформанса, а не журнал конкретного запуска. Действующие
[буквальные примеры](../tests/fixtures/fs/cases.json) и
[проверки](../tests/test_fs.py) исполняют общий контракт, реальные read/next/write
и выбор политики. Они сравнивают полные ответы, целевые обращения, байты,
собственное состояние и сохранённую конфигурацию.
Ожидаемые значения не получают из production constants или evaluator.

## Правильное использование

1. Подготовьте существующие каталоги roots и базу операции в поддержанном окружении.
2. Укажите версию и оба read/write-разрешения каждого уникального root в рабочем `paths`.
3. Используйте поддержанный файловый инструмент с объявленным policy binding и его
   действительной публичной схемой; адаптер подготовит полный запрос политики.
4. Выполняйте целевое действие после допуска всеми применимыми владельцами.
   Для нового файла заранее подготовьте его родительский каталог назначения.
5. Обрабатывайте business result провайдера согласно его schema и сохранённому
   результату запроса; версии конфигурации выбирайте явно.

Пример ниже выполняет правильную последовательность на отдельной копии приложения.

## Полный пример самостоятельной установки

В Bash из корня checkout задайте `POISE_PYTHON` абсолютным путём CPython 3.13.
Пример копирует только приложение Poise и опубликованные конфиги в отдельный
временный каталог. Значения roots меняются только в этой рабочей копии.

```bash
# filesystem-quickstart
set -e
POISE_FS_ROOT="$(mktemp -d)"
cp -R apps/poise "$POISE_FS_ROOT/poise"
mkdir -p "$POISE_FS_ROOT/config" "$POISE_FS_ROOT/workspace/reference"
cp "$POISE_FS_ROOT/poise/config/filesystem.example.json" "$POISE_FS_ROOT/config/poise.json"
cp "$POISE_FS_ROOT/poise/config/state.example.json" "$POISE_FS_ROOT/config/state.json"
"$POISE_PYTHON" - "$POISE_FS_ROOT" <<'PYCLIENT'
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
workspace = root / 'workspace'
config_path = root / 'config/poise.json'
config = json.loads(config_path.read_text())
config['paths']['roots'][0]['path'] = str(workspace)
config['paths']['roots'][1]['path'] = str(workspace / 'reference')
config['paths']['operator_note'] = {'text': 'Справочник доступен для чтения'}
config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
state_path = root / 'config/state.json'
state = json.loads(state_path.read_text())
state['database'] = config['session_store']
state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
(workspace / 'reference/guide.txt').write_text('Guide\n', encoding='utf-8')
(workspace / 'existing.txt').write_text('Before\n', encoding='utf-8')
config_bytes = config_path.read_bytes()
environment = os.environ.copy()
for name in ('CODEX_SESSION_ID', 'CODEX_THREAD_ID'):
    environment.pop(name, None)
calls = []
facts = {}

def save():
    (root / 'example-calls.json').write_text(json.dumps(
        {'calls': calls, 'facts': facts}, ensure_ascii=False, indent=2) + '\n')

def call(operation, value, *, state=False):
    name = 'poise-state' if state else 'poise'
    schema = 'poise-state/v1' if state else 'stride-poise/v1'
    packet = {'schema': schema, 'operation': operation, 'input': value}
    payload = json.dumps(packet, ensure_ascii=False) + '\n'
    argv = [sys.executable, '-I', '-S', '-B', str(root / 'poise/bin' / name),
            '--config', str(state_path if state else config_path)]
    child = subprocess.run(argv, input=payload.encode('utf-8'),
        capture_output=True, env=environment, cwd=root, check=False)
    calls.append({'argv': argv, 'cwd': str(root), 'request': packet, 'stdin': payload,
        'exit': child.returncode, 'stdout': child.stdout.decode('utf-8'),
        'stderr': child.stderr.decode('utf-8')})
    save()
    child.check_returncode()
    print(child.stdout.decode('utf-8'), end='')
    return json.loads(child.stdout)['result']

call('paths.evaluate', {'schema': 'poise-fs-request/v1', 'base': str(workspace),
    'effects': [{'operation': 'read', 'path': 'reference/guide.txt'},
                {'operation': 'write', 'path': 'sample.txt'}]})
call('files.write', {'schema': 'poise-write-request/v1', 'base': str(workspace),
    'writes': [{'path': 'sample.txt', 'text': 'A\nЖ🙂\nZ'},
               {'path': 'existing.txt', 'text': 'Ж\n'}]})
facts['before_session'] = {
    'database_exists': (root / 'config' / config['session_store']).exists(),
    'binding_exists': (root / 'config' / config['caller']['binding_file']).exists()}
facts['sample_modified_ns'] = (workspace / 'sample.txt').stat().st_mtime_ns
save()
call('prepare', {}, state=True)
call('session.ensure', {})
context = call('reader.context', {})
portion = call('reader.read', {'cwd': str(workspace), 'generation': context['generation'],
    'acknowledged': [], 'reads': [{'path': 'sample.txt',
        'range': {'unit': 'lines', 'start': 1, 'count': 1},
        'reread': False, 'reason': ''}]})
while not portion['items'][0]['eof']:
    item = portion['items'][0]
    portion = call('reader.next', {'generation': context['generation'],
        'acknowledged': [item['receipt_id']], 'reads': [
            {'cursor': item['next'], 'count': 1, 'reread': False, 'reason': ''}]})
facts['config_unchanged'] = config_path.read_bytes() == config_bytes
facts['sample_hex'] = (workspace / 'sample.txt').read_bytes().hex()
facts['existing_hex'] = (workspace / 'existing.txt').read_bytes().hex()
facts['reference_hex'] = (workspace / 'reference/guide.txt').read_bytes().hex()
save()
PYCLIENT
printf 'Filesystem example: %s\n' "$POISE_FS_ROOT"
```

Все восемь вызовов имеют exit `0`, пустой stderr и `status: "ok"`.
Расчёт возвращает `allow` с roots `reference, workspace` для чтения справочника
и `workspace` для записи. Запись создаёт `sample.txt` из 10 UTF-8 байтов и заменяет
`existing.txt` тремя байтами `Ж\n`; справочник сохраняется. До подготовки состояния
файлы SQLite и caller binding отсутствуют. Подготовка даёт версию `1`, затем
создаётся собственная generated Session без роли и reader-контекст поколения `1`.
Чтение и два продолжения возвращают `A\n`, `Ж🙂\n`, `Z`; последняя порция имеет
`eof: true` и `next: null`. Пример передаёт фактически полученные IDs и курсоры.

Очистка native-переменных относится только к отдельным дочерним процессам
собственного generated-примера. Native identity текущей оболочки сохраняется.
Приложение оставляет рабочую конфигурацию, включая неизвестный `operator_note`,
без перезаписи. Полные argv, cwd, запросы, выходы и наблюдения находятся в
`example-calls.json` указанного каталога. Для дальнейшей работы сохраняйте свою
конфигурацию, состояние и caller binding, получайте текущее поколение reader
и передавайте полученные квитанции после приёма текста.

## Проверка реализации

Из корня checkout задайте `POISE_FS_CHECK_OUTPUT` абсолютным путём каталога отчётов:

```bash
"$POISE_PYTHON" -B apps/poise/tests/test_fs.py --suite delivery \
  --output-root "$POISE_FS_CHECK_OUTPUT/delivery"
"$POISE_PYTHON" -B apps/poise/tests/test_fs.py --suite behavior \
  --output-root "$POISE_FS_CHECK_OUTPUT/behavior"
```

Оба вызова имеют exit `0` на текущем коде. `delivery` проверяет реальный прямой
вход и эффект записи. `behavior` исполняет четыре группы: общую политику,
чтение/продолжение, запись и явный выбор конфигурации. Полные наблюдения
сохраняются в `fs-details.json` каждого каталога. Они включают точные ответы,
целевые обращения, байты, состояние и конфигурацию. Эти запуски отдельно от
проверки синтаксиса и ссылок подтверждают поведение приложения.


## Состояние, наблюдаемость и пределы

Владелец политики не хранит Sessions, Tasks, receipts, request lifecycle или свой
журнал разрешений. Один расчёт использует одну явную конфигурацию и подготовленный
набор путей. Обратная проверка результата связывает каждый `index`, операцию,
фактический путь и список roots с тем же входным эффектом и настройками.

Наблюдаемый результат — структурированное решение, root IDs и error pointer;
содержимое файлов для него не требуется. Выполнение принадлежит выбранному
провайдеру, нужный ответ сохраняет вызывающий. Reader отдельно хранит собственные
метаданные. Новая служба логирования или учёта файловых запросов не вводится.

Обычное разрешение metadata и последующий файловый эффект не образуют общую
ОС-транзакцию. Контракт не заявляет защиты от конкурентной подмены путей или
полного OS sandbox. Последующая ошибка разрешённого I/O не превращается в
автоматический rollback. Подключение каждого read/write provider доказывается
на его реальном входе; источник текущей схемы и документ не заменяют эту проверку.

## Связь с требованиями

| Обязательство | Действующий договор и проверка |
| --- | --- |
| R01 / POI-04 | Конфигурация, точные вход/результат/ошибки, F1–F16, совместные ограничения и фактическое назначение. |
| R02 / POI-02 | Тот же полный per-root контракт и F1–F16; владельцы/enforcement, forward/reverse связь с provider, Task-независимый вход и границы T016. |
| R03 / POI-03 | Прямые расчёт и запись, выбранный reader и полный пример отдельной установки; явные команды без Task/Stage и обязательного generic gateway. |
| DoD: внутри/вне, overlap, symlink | F1–F15, одинаковые правила roots, neutral вне всех roots, обычные ссылочные назначения. |
| DoD: co-delivery | Этот app-owned документ содержит выбранные правила, интерфейс, правильную конфигурацию и инструкции; отдельная документационная задача не требуется. |

Проверка ссылок подтверждает локальные пути и anchors. Реальные запуски
примера и файловых проверок отдельно подтверждают исполнение описанных операций.
