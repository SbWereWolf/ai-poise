# Самостоятельный CLI Requirements

Обновлено: **2026-10-17**.

## Запуск и окружение

Используйте Python 3.13 (`>=3.13,<3.14` в `pyproject.toml`) со стандартным модулем
`sqlite3` и зависимостями проекта. Инструкция относится к checkout, содержащему
эту реализацию. Проверки выполнены на Python 3.13.15 и SQLite 3.53.1.

Точка входа приложения:

```text
apps/requirements/bin/requirements --config /absolute/path/to/requirements.json
```

Интерпретатор можно выбрать явно:

```sh
"$requirements_python" -B "$requirements_checkout/apps/requirements/bin/requirements" \
  --config "$requirements_work/config/requirements.json"
```

Исполняемый файл разрешает корень своих исходников и `src` относительно собственного
расположения. `--config` задаётся вызывающей стороной. Приложение принимает один
UTF-8 JSON object через stdin и возвращает один JSON object с завершающим переводом
строки через stdout. Успех соответствует exit 0, описанный отказ — exit 2.

Текущий пакет содержит `operation` и `input`. Реализованы `create_task`, `apply`, `query` и
`import_bootstrap`. Через `query` доступны трёхуровневый граф, текущие и сохранённые
постановки, история и поиск; через `apply` — изменение известных требований и
полных постановок Task. Правильные пакеты и предусловия приведены в
[руководстве графа](graph-req.md). [Публичный проект контракта](contract-req.md) описывает также
будущие операции; фактический запуск и пакет текущей версии определяет эта страница.

## Конфигурация приложения

Используйте полный объект `stride-requirements-config/v1`:

```json
{
  "schema": "stride-requirements-config/v1",
  "paths": {
    "state": "../state",
    "database": "requirements.sqlite",
    "lock": "requirements.lock"
  },
  "batch": {
    "max_input_bytes": 8192,
    "max_items": 64
  },
  "lock": {
    "seconds": 1.0,
    "poll_seconds": 0.01
  },
  "task_ids": {
    "namespace": {"minimum": 1, "maximum": 999999},
    "width": 6,
    "progression": {"first": 1, "step": 1}
  }
}
```

| Параметр | Правило заполнения |
| --- | --- |
| `paths.state` | Абсолютный путь или путь относительно каталога конфигурации. |
| `paths.database`, `paths.lock` | Различные относительные пути внутри `state`; собственные database и lock приложения. |
| `batch.max_input_bytes` | Положительное целое число: предел байтов входного пакета. |
| `batch.max_items` | Положительное целое число: предел элементов одного пакета. |
| `lock.seconds`, `lock.poll_seconds` | Положительные конечные числа: ожидание и интервал проверки локальной границы записи. |
| `task_ids` | Целые `minimum >= 0`, `maximum >= minimum`, `first` внутри namespace; положительные целые `width` и `step`. Ширина вмещает десятичную запись maximum. Проверяется при каждом запуске. |

Используйте одну выбранную конфигурацию для последующих обращений к одному
хранилищу. Каталоги `state` и его файлов должны быть доступны процессу для записи.
Каждая установка Requirements владеет своим состоянием; конфигурация соседнего
приложения (`neighbor`) относится к его отдельному владельцу.

## Настройка выдачи Task ID

При автоматическом создании `task_ids.progression.first` задаёт первого кандидата,
`step` — положительный шаг, `namespace.minimum/maximum` — включительные границы,
а `width` — ширину десятичной строки с ведущими нулями. Выбирается первый свободный
кандидат; занятые IDs сохраняются. Рассчитывайте вместимость пространства по своей
нагрузке; для расширения оператор явно согласует `maximum`, `width` и progression
в рабочей конфигурации. Описанный отказ при исчерпании сохраняет существующие данные.

В `create_task` передавайте `request_id` и содержательный `goal`. При явном
`task_id` Requirements сохраняет указанную свободную строку; числовая progression
определяет автоматическую выдачу. Передавайте выданный ID соседним приложениям
точно как строку. Конфигурация целиком проверяется при каждом вызове, включая повтор
создания; корректное изменение политики сохраняет прежний результат точного повтора,
а новое создание использует текущую политику.

[Полная последовательность создания, связывания и повтора](graph-req.md#создание-task-и-канонический-id)
содержит действующие пакеты и значения ответов. Для выбора формы передавайте
явный `work_choice`; Requirements сохраняет декларацию в первоначальном намерении.
Обслуживание конфигурации и восстановление данных выполняет отдельный инструмент
оператора; рабочие настройки остаются вне исходников приложения.

## Создание и повторное открытие

Выберите новый каталог для примера и задайте три фактических абсолютных пути:

```sh
requirements_python=/absolute/path/to/python3.13
requirements_checkout=/absolute/path/to/ai-poise
requirements_work=/absolute/path/to/requirements-example
export PATH="$(dirname "$requirements_python"):$PATH"
mkdir -p "$requirements_work/config"
cp "$requirements_checkout/apps/requirements/config/requirements.example.json" \
  "$requirements_work/config/requirements.json"
```

Первый запрос подготавливает собственное хранилище формата 1 с ревизией 0.
`read_only` описывает запрос к требованиям: создание структуры при первом
открытии входит в подготовку хранилища.

```sh
"$requirements_python" -B "$requirements_checkout/apps/requirements/bin/requirements" \
  --config "$requirements_work/config/requirements.json" <<'JSON'
{"operation":"query","input":{"queries":[{"id":"registry","kind":"registry"}]}}
JSON
```

Результат:

```json
{"status":"read_only","revision":0,"results":[{"id":"registry","value":{"requirements":{},"links":[]}}]}
```

Сохраните пакет изменения:

```sh
cat > "$requirements_work/create.json" <<'JSON'
{
  "operation": "apply",
  "input": {
    "request_id": "example-create",
    "expected_revision": 0,
    "operations": [
      {"kind":"put_requirement","requirement":{"id":"SYS-KEEP","level":"system","status":"current","text":"Сохранять требования."}},
      {"kind":"put_requirement","requirement":{"id":"APP-STORE","level":"application","status":"current","text":"Открывать самостоятельное хранилище."}},
      {"kind":"link","system":"SYS-KEEP","application":"APP-STORE"}
    ]
  }
}
JSON
"$requirements_python" -B "$requirements_checkout/apps/requirements/bin/requirements" \
  --config "$requirements_work/config/requirements.json" < "$requirements_work/create.json"
```

Ответ содержит `status:applied`, `revision:1`, `replayed:false`. Повторите первый
запрос `query` с той же конфигурацией: новый процесс повторно откроет файл и вернёт
оба требования и их связь на ревизии 1. Повторное исполнение сохранённого
`create.json` вернёт `revision:1`, `replayed:true` с сохранением данных.

## Операции и данные

`apply` получает `request_id`, `expected_revision` и упорядоченный список
`operations`. Поддержаны `put_requirement` и `link` между System и Application.
Для нового изменения выбирайте новый `request_id` и актуальную ревизию из ответа
`query`. Для повтора передавайте тот же пакет: порядок операций является частью
намерения, порядок ключей JSON objects на его равенство не влияет.

`query` получает список `queries`. Доступны запросы реестра и существующие
доменные запросы, включая `plan_task`, а также типизированные страницы сущностей
и связей через общий владелец запросов. Каталог предметных сущностей доступен в
новом хранилище с подготовленными metadata-таблицами.

`import_bootstrap` получает абсолютный путь `path` к документу
`requirements-bootstrap-1` и `request_id`. Он публикует начальные требования и
связи в пустое хранилище с ревизией 0 через тот же `apply`.

[Формат хранения и публичная композиция](storage-req.md) описывают модель,
повторное открытие и поддерживаемые методы. `create_task` сохраняет ID и первую
постановку; `revise_task` записывает её следующую редакцию. Текущая постановка,
история, исходное создание и сохранённые основания читаются через
[действующий профиль графа](graph-req.md#создание-task-и-канонический-id).
`plan_task` вычисляет предлагаемый контекст из реестра требований;
purpose-aware публикация и outcomes остаются будущими возможностями.

Для регистрации отдельной будущей потребности и её последующего явного включения
в Task используйте [последовательность текущих и будущих обязательств](graph-req.md#текущее-обязательство-и-будущая-потребность).
Она показывает полную следующую постановку, чтение прежнего текста, актуальные
верхние значения и оценку содержательного основания либо пустого placeholder.

## Ошибки и продолжение работы

Описание отказа возвращается в `reason` вместе с `status:rejected` и exit 2.
Конфигурация и пакет проверяются до открытия хранилища. Ошибки подготовки,
занятость SQLite и ошибки файловой системы возвращаются вызывающей стороне;
изменения схемы и строк внутри незавершённой транзакции откатываются.

Для продолжения обеспечьте доступность выбранного каталога, корректные входные
данные и завершение конкурирующей записи, затем повторите нужную операцию.
Восстановление повреждённых файлов выполняет оператор отдельным инструментом.
Повтор конкретного запроса использует читаемые сохранённые JSON objects намерения
и ответа. Ошибка этих данных относится к этому повтору; она не вводит общую
проверку целостности при запуске остальных запросов.

## Проверки

Проверки фактического CLI и документации:

```sh
"$requirements_python" -B "$requirements_checkout/apps/requirements/tests/test_shell.py" --suite behavior
"$requirements_python" -B "$requirements_checkout/apps/requirements/tests/test_shell.py" --suite documentation
```

Проверки хранилища и потребителя:

```sh
"$requirements_python" -B "$requirements_checkout/apps/requirements/tests/test_storage.py"
"$requirements_python" -B "$requirements_checkout/apps/requirements/tests/test_storage_consumer.py"
```
