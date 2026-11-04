# Доставка инструкций в текущий контекст

Обновлено: **2026-11-04**.

`skills/deliver` получает инструкции для выбранных файлов, навыков этапа и одного
инструмента. Полное наблюдение сохраняется для машинного потребителя; отдельный пакет
доставки повторяет тексты, необходимые текущему контексту. Общий пакет предоставляет также
[переносимый Python API](#переносимый-python-api)
для готового выбора и [доставку с бюджетом](#доставка-с-бюджетом). Операция `prepare` сохраняет
[свой договор полного наблюдения](development-routing.md#подготовка-инструкций-по-файлам).

## Вызов и подготовка

Нужны выбранная поставка Poise с операцией `deliver`, её Python, общий профиль
`poise-harness-project-1`, зарегистрированный Git checkout, каталог навыков, корень
их инструкций и policy выбора. Профиль получают через
[владельца настройки проекта](project-setup.md#обновление-действующего-проекта).
Task, Task adapter, bootstrap и БД задач для этой операции не требуются.

`--catalog` и `--root` задают каталог и корень инструкций независимо от checkout.
Правила `AGENTS.md` берутся из предков выбранных файлов checkout, навыки — из корня
инструкций. Профили и обязательность сохраняют
[обычный договор выбора](development-routing.md#выбор-содержимое-и-готовность).
Назначенные навыки этапа передаются в `stage.skill_ids`, становятся обязательными,
а их области и причины объединяются с уже выбранными записями.

Заполните рабочую policy по
[образцу](../../config/examples/project-instructions.example.json) и сохраните вне Git.
Все её параметры описаны в
[настройке подготовки](development-routing.md#настройка-и-вызов).
Например, полный минимальный профиль выбора для каталога самой Poise:

```json
{
  "schema": "poise-project-instruction-policy-1",
  "version": "delivery-1",
  "universal_skills": ["preflight"],
  "profiles": []
}
```

Для установленной выбранной поставки:

```bash
poise=( "${poise_python:?Укажите выбранный Python Poise}" -I -B -m poise )
"${poise[@]}" skills --catalog "$skill_catalog" --root "$instruction_root" < "$packet"
```

Переменные заранее задаются абсолютными путями. Для явно выбранной source-поставки
выберите её `src` через `PYTHONPATH` и `-P -s -B` вместо `-I`, согласно
[инструкции запуска](development-routing.md#настройка-и-вызов).
Обновление независимых установок и публикация пакета остаются отдельными операциями.

## Запрос

Безбюджетная файловая форма stdin содержит ровно десять обязательных полей; `previous` и `tool`
допускают `null`.

| Поле | Договор |
|---|---|
| `operation` | `deliver`. |
| `project_config` | Абсолютный путь общего профиля выбранного проекта. |
| `checkout` | Абсолютный корень настроенного checkout либо его зарегистрированного worktree. |
| `policy` | Абсолютный путь policy выбора инструкций. |
| `paths` | Непустой список конкретных относительных POSIX-путей. Пути объединяются и сортируются; рабочий файл может ещё создаваться. |
| `stage` | Ровно непустой `id` и уникальные `skill_ids` фактически назначенных навыков; список сортируется, явный пустой список допустим. |
| `context` | Ровно непустой `id`, положительное целое `generation` и `reason`: `initial`, `continue`, `resume` или `compaction`. |
| `previous` | Полный ранее полученный `receipt` этой операции либо `null`. |
| `retained` | Уникальные ключи материалов, доступность полных текстов которых вызывающая сторона подтверждает сейчас; явный пустой список допустим. |
| `tool` | `null` либо ровно непустой `id`, `root` (`checkout` или `instructions`) и относительный `path` краткого руководства. |

`context.id` — метка контекста доступного текста, `generation` — его поколение.
Это данные операции, отдельные от native caller/session identity. Фактическую причину
продолжения и поколение передаёт вызывающая сторона или подключённый адаптер. Операция
сама не устанавливает hooks и не определяет, какие тексты сохранила модель.

Пример первоначального запроса:

```json
{
  "operation": "deliver",
  "project_config": "/srv/operator/general-project.json",
  "checkout": "/srv/application",
  "policy": "/srv/operator/instruction-policy.json",
  "paths": ["src/app.py"],
  "stage": {"id": "implementation", "skill_ids": ["repo-tooling"]},
  "context": {"id": "work-context-1", "generation": 1, "reason": "initial"},
  "previous": null,
  "retained": [],
  "tool": {
    "id": "skills",
    "root": "instructions",
    "path": ".agents/references/skills-delivery.md"
  }
}
```

Замените адреса фактическими. Каталог и корень инструкций этого примера выбирают
поставку Poise, содержащую `preflight`, `repo-tooling` и
[краткое руководство](../../.agents/references/skills-delivery.md).
Файл выбранного инструмента читается целиком тем же владельцем источников. Он содержит
краткую инструкцию и прямую ссылку на подробную справку. Выбирается один явный адрес;
отдельного реестра инструментов и извлечения команд из Markdown нет.

## Ответ и receipt

Безбюджетный файловый ответ содержит ровно `schema`, `status`, `observation`, `delivery`, `receipt`.
`schema` равна `poise-instruction-delivery-1`, `status` — `ready` либо `needs_inputs`.

| Поле | Содержание |
|---|---|
| `observation` | Полное актуальное наблюдение: `schema`, `status`, `project`, `checkout`, `instruction_root`, `policy_sha256`, `paths`, все `materials`, `diagnostics`, `delta: null`. Учитывает навыки этапа и выбранное руководство. |
| `delivery` | `project`, `checkout`, `instruction_root`, текущие `context`, `stage`, `paths`, полные записи `materials`, метаданные `reused`, `diagnostics`. |
| `receipt` | Ровно `schema`, `project`, `checkout`, `instruction_root`, `context` с `id`/`generation`, `stage`, `paths`, `materials`. Схема — `poise-instruction-delivery-receipt-1`. |

Каждый выбранный материал появляется ровно в одной коллекции `delivery.materials`
или `delivery.reused`. Полная запись содержит `kind`, `id`, `root`, `path`, `scopes`,
`required`, `reasons`, `available`, `sha256`, `content`. В `reused` отсутствует только
`content`; полный текст остаётся в `observation`. Порядок — по `kind`, `id`, `root`, `path`.
Для выбранного руководства `kind` равна `tool`.

Недоступная запись остаётся в `materials` с `available: false`, `sha256: null`,
`content: null` и адресной диагностикой. Недоступный рекомендованный материал сохраняет
`ready`; обязательный даёт `needs_inputs`.

Записи `receipt.materials` имеют ровно `kind`, `id`, `root`, `path`, `sha256`, отсортированы
по идентичности и включают только доступные источники. Ключ материала — `kind:id:root:path`.
В `previous` передаётся именно полный receipt. Он имеет отдельную схему от comparison
`previous` операции `prepare`; `observation` служит полному наблюдению источников.
Receipt хранит версионные сведения, а подтверждение текущей доступности передаётся отдельно.

## Повтор и продолжение

Повторное использование требует одновременно:

1. `reason: continue` и совпадающих `context.id`/`generation`.
2. Receipt того же проекта, checkout и корня инструкций, с текущими этапом и путями.
3. Явного подтверждения ключа в `retained`.
4. Доступного текущего источника с тем же SHA-256.

Сначала получите и прочитайте полные требуемые тексты через выбранного потребителя.
Затем передайте полученный receipt и подтвердите только ключи текстов, доступных
в том же контексте. При частичном подтверждении доставляется оставшаяся часть.
Сохранение полного результата или receipt само по себе не подтверждает доступность текста.

Новая версия доставляется актуальным текстом. Смена области, набора путей, этапа,
его навыков, context id или поколения требует полной текущей доставки. В форме с бюджетом
текст поступает через [страницы или адресное чтение](#доставка-с-бюджетом).
При фактическом resume передайте `reason: resume`, после сжатия — `reason: compaction`;
оба значения требуют полной доставки независимо от исторического receipt.
Новый контекст получает новую метку/поколение и `reason: initial`.

При неизвестной доступности используйте пустой `retained`. При отсутствии пригодного
receipt используйте `previous: null`: результатом будет полная текущая доставка,
включая случай уже переданных корректно типизированных подтверждений. При существующем
receipt подтверждаемые ключи принадлежат ему. История чтения и неизменный hash сами
по себе не заменяют текущего подтверждения.

## Установка выбранной поставки

Переносимый интерфейс `poise.instructions.execute` входит в development-поставку
`ai-poise` версии `2.2.0.dev0`. Для работы нужен Python **3.15** (`>=3.15,<3.16`).
Выберите принятую неизменяемую Git revision либо проверенный wheel; запишите её
в зависимостях/lock потребителя вместе с версией. Владельцы интеграции и поставки
сохраняют фактическую принятую ревизию. Наличие development-версии в исходниках
не означает её публикацию в пакетном индексе.

Рабочее приложение устанавливает зависимость своим пакетным менеджером в собственное
окружение. Конфигурацию, policy и источники инструкций задаёт сам потребитель.
Исходная интеграция, выпуск пакета и обновление каждого потребителя следуют
[общему договору версий](stride-launcher.md#библиотечные-потребители-и-версии).

Для воспроизводимой проверки выбранного wheel используйте отдельный каталог
установленной зависимости. Следующая процедура требует заранее подготовленных
build/runtime-зависимостей выбранного окружения: `setuptools>=80.9.0`, `psutil`,
`yoyo-migrations==9.0.0`, `sqlparse==0.6.0`. `supply_source` содержит точные исходники
выбранной ревизии, подготовленные их владельцем; `wheel_dir` и новый `dependency_prefix`
принадлежат проверке потребителя. Все пути абсолютные, каталоги подготовлены владельцем.

```bash
"${build_python:?Укажите выбранный Python 3.15}" -I -m pip wheel \
  --no-deps --no-build-isolation --no-index \
  --wheel-dir "${wheel_dir:?}" "${supply_source:?}"

"${consumer_python:?Укажите Python 3.15 потребителя}" -I -m pip install \
  --no-deps --no-index --target "${dependency_prefix:?}" "${selected_wheel:?}"
```

`selected_wheel` — точный файл результата сборки выбранной версии; `selected_version`
заранее задают точной версией этого пакета (`2.2.0.dev0` для описанной поставки). Зафиксируйте его
SHA-256 и исходную Git revision. Сборка без build isolation использует подготовленные
build-зависимости; установка с `--no-deps` использует уже подготовленные runtime-зависимости.
Это явные условия процедуры, а не автоматическое обновление окружения.

Из каталога вне исходного checkout проверьте установленный префикс и версию:

```bash
"$consumer_python" -I -B - "$dependency_prefix" "$selected_version" <<'PY'
import importlib.metadata
import sys
from pathlib import Path

prefix = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(prefix))
import poise.instructions

assert importlib.metadata.version("ai-poise") == sys.argv[2]
assert Path(poise.instructions.__file__).resolve().is_relative_to(prefix)
print(poise.instructions.__file__)
PY
```

В этом проверочном процессе добавляется только префикс действительно установленного
пакета. Обычное приложение получает библиотеку из своего настроенного окружения:
`from poise.instructions import execute`. При подготовке к подключению сохраните
версию, выбранную ревизию/хеш wheel и результат проверки происхождения модулей.
Доставка общих исходников сохраняет возможность построить выбранную поставку после
очистки временных материалов проверки. Развёртывание потребителя выполняет его владелец.

## Переносимый Python API

Потребитель с уже выбранными материалами вызывает `poise.instructions.execute(request)`
или передаёт тот же JSON операции `skills` без `--catalog`/`--root`:

```bash
"${poise[@]}" skills < "$selected_packet"
```

Для этого маршрута требуются готовое наблюдение и данные текущего контекста. Настройка
выбора источников, Task, общий профиль и БД Poise здесь не участвуют. Файловый `deliver`
продолжает использовать [свою подготовку](#вызов-и-подготовка), наблюдает источники один
раз и передаёт результат тому же общему владельцу доставки.

Запрос `deliver_selected` содержит ровно девять обязательных полей:

| Поле | Значение |
|---|---|
| `schema` | `poise-portable-instruction-request-1`. |
| `operation` | `deliver_selected`. |
| `selection` | Полное текущее наблюдение по договору ниже. |
| `context`, `stage` | [Данные контекста и этапа](#запрос); `generation` — положительное целое, отдельное от Python `bool`. |
| `previous`, `retained` | Полный receipt либо `null` и уникальные подтверждённые ключи; [обычные условия повторного использования](#повтор-и-продолжение). |
| `budget` | Ровно `max_bytes`: положительное целое, отдельное от `bool`. |
| `continuation` | `null` либо полученное непрозрачное значение продолжения. |

Все поля передаются явно. `selection` имеет ровно `schema`, `status`, `project`,
`checkout`, `instruction_root`, `policy_sha256`, `paths`, `materials`, `diagnostics`,
`delta`. Его schema — `poise-project-instructions-1`, `delta` — `null`;
`checkout`/`instruction_root` — явные абсолютные адреса владельца источников,
`policy_sha256` — SHA-256 фактической версии policy, `paths` — непустой канонический
список конкретных относительных POSIX-путей. Метаданные нормализованного наблюдения
сортируются заранее; нормализованный вход проверяет их, а не ищет файлы заново.

Каждая запись `materials` содержит ровно `kind`, `id`, `root`, `path`, `scopes`,
`required`, `reasons`, `available`, `sha256`, `content`. `kind` принимает `rule`,
`profile`, `skill`, `tool`; rules/profiles используют `checkout`, skills — `instructions`,
tool — один из этих корней. `id` — непустой идентификатор без `:`, `path` — конкретный
относительный POSIX-путь с разделителем `/`, без пустых/`.`/`..` сегментов, `:`,
обратной косой черты и символов шаблонов `*`, `?`, `[` и `]`. `scopes` — непустой
отсортированный список уникальных выбранных `paths`; `reasons` — непустой отсортированный
список уникальных причин. `required`/`available` имеют тип boolean. Материалы уникальны
и упорядочены по `kind`, `id`, `root`, `path`.

Доступная запись содержит полный Unicode-текст и его SHA-256 от UTF-8 bytes.
Недоступная запись содержит `content: null`, `sha256: null`. Диагностика и status
соответствуют этой доступности: обязательный недоступный материал даёт `needs_inputs`,
иначе — `ready`. Запись диагностики имеет `code`, `key`, `action`; коды
`missing_required_material`/`missing_recommended_material` используют
`restore:<root>:<path>`. Диагностика упорядочена по этим трём полям.

Полный пример уже выбранного наблюдения. Литералы policy/body представляют исходные
байты демонстрационного источника; рабочий selector передаёт собственные реальные данные.

```python
import hashlib
from poise.instructions import execute

body = "Проверяйте итог операции.\r\n"
paths = ["src/app.py"]
material = {
    "kind": "rule", "id": "AGENTS.md", "root": "checkout", "path": "AGENTS.md",
    "scopes": paths, "required": True, "reasons": ["ancestor:src/app.py"],
    "available": True, "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    "content": body,
}
selection = {
    "schema": "poise-project-instructions-1", "status": "ready", "project": "demo",
    "checkout": "/srv/application", "instruction_root": "/srv/instructions",
    "policy_sha256": hashlib.sha256(b"explicit-demo-policy").hexdigest(),
    "paths": paths, "materials": [material], "diagnostics": [], "delta": None,
}
request = {
    "schema": "poise-portable-instruction-request-1", "operation": "deliver_selected",
    "selection": selection,
    "context": {"id": "demo-context", "generation": 1, "reason": "initial"},
    "stage": {"id": "implementation", "skill_ids": []},
    "previous": None, "retained": [], "budget": {"max_bytes": 8192}, "continuation": None,
}
result = execute(request)
assert result["status"] == "complete"
assert result["delivery"]["materials"][0]["content"] == body
print(result["delivery"]["materials"][0]["content"], end="")
```

После получения и чтения полного текста этого примера в том же контексте вызывающая
сторона может подтвердить именно его ключ:

```python
request["previous"] = result["receipt"]
request["retained"] = ["rule:AGENTS.md:checkout:AGENTS.md"]
request["context"]["reason"] = "continue"
repeated = execute(request)
assert repeated["delivery"]["materials"] == []
assert repeated["delivery"]["reused"][0]["sha256"] == material["sha256"]
```

При неизвестной текущей доступности передают пустой `retained`. Версионный receipt
описывает источники и сам по себе доступность их текста не подтверждает.

## Доставка с бюджетом

У файлового `deliver` ограниченная форма содержит ровно двенадцать полей:
[исходные десять](#запрос), `budget` и `continuation`. Для первого обращения добавьте
`"budget": {"max_bytes": 8192}` и `"continuation": null`. Безбюджетная десятипольная
форма сохраняет свой прежний ответ. Нормализованный вход использует
[описанные девять полей](#переносимый-python-api).

`max_bytes` ограничивает только `delivery`, сериализованный как компактный JSON:
`ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`, UTF-8, без завершающего
LF. Полные `observation`, `receipt`, stdout/stderr и внешний envelope сохраняются.
Настройки [общего представления](#полный-результат-и-агентское-представление) отдельно
допускают отображаемый результат.

Вначале должны поместиться сведения навигации и обязательные метаданные. Если итоговый
пакет со всеми доступными неповторяемыми текстами помещается, выдаётся этот полный
пакет. Иначе материалы рассматриваются в каноническом порядке и добавляются целиком,
когда фактический пакет помещается в бюджет. Слишком большой материал остаётся для
адресного чтения; следующие подходящие материалы могут быть выданы сейчас.

Ответ ограниченной формы содержит ровно `schema`, `status`, `observation`, `delivery`,
`receipt`; schema — `poise-portable-instruction-result-1`. Полное наблюдение и
[версионный receipt](#ответ-и-receipt) сохраняют свой договор.

| `status` | Наблюдаемый результат |
|---|---|
| `complete` | Все доступные выбранные тексты выданы целиком либо подтверждены как доступные сейчас. |
| `partial` | Остались доступные тексты для следующей страницы или адресного чтения. |
| `needs_inputs` | Есть недоступный обязательный источник; диагностика и доступные остатки сохранены. |
| `read` | Выполнено отдельное адресное чтение. |
| `rejected` | Получен адресный отказ с `error`. |

Frame `delivery` при доставке имеет ровно `schema`, `mode`, `status`, `project`,
`checkout`, `instruction_root`, `context`, `stage`, `paths`, `materials`, `reused`,
`remaining`, `continuation`, `diagnostics`. Его schema —
`poise-portable-instructions-frame-1`, `mode` — `deliver`. `materials` содержит полные
выданные записи, `reused` — подтверждённые метаданные без `content`. `remaining`
имеет записи ровно с `key`, `required`, `sha256`, `address`, `reason`:

- Доступный остаток: полный hash, непрозрачный `address`, `reason: budget`.
- Недоступный источник: null hash/address, `reason: unavailable`, адресная диагностика.

Недоступные рекомендованные источники остаются видимыми и допускают `complete`.
При недоступном обязательном источнике приоритет имеет `needs_inputs`.
`continuation` непуст ровно тогда, когда остались доступные тексты; иначе — `null`.
Он привязан к текущим источникам, policy, метаданным, context id/generation, этапу,
путям и бюджету; переход причины `initial` → `continue` сохраняет эту привязку.

Для обычной следующей страницы сохраните полученный receipt и continuation, подтвердите
только полные тексты, действительно доступные сейчас, и повторите доставку с
`reason: continue`. Оставшийся материал, помещающийся отдельно, выдаётся целиком.
Повтор с теми же входами без подтверждения предыдущего текста может выдать ту же страницу.
Continuation сам не подтверждает текст и не хранит скрытый курсор.

При изменении источников/метаданных, области, этапа, context id/generation или бюджета
получите свежую доставку с `continuation: null`. После реального resume/compaction
передайте фактическую причину перехода и актуальные данные владельца контекста;
требуемые тексты снова подлежат полной доставке через страницы или адресное чтение.

## Адресное чтение

`read_selected` читает материал из переданного полного наблюдения по ранее полученному
адресу. Это операция над выбранным содержимым, со своим бюджетом; filesystem и историю
чтения продолжает обслуживать их существующий владелец.

Запрос содержит ровно восемь полей: `schema`, `operation`, `selection`, `context`,
`stage`, `address`, `range`, `budget`. Schema — `poise-portable-instruction-request-1`,
operation — `read_selected`. Выберите фактически выданный адрес доступного остатка.
Наблюдение, контекст и этап должны соответствовать его текущей версии; для чтения
можно задать другой явный бюджет.

| `range` | Договор |
|---|---|
| `null` | Полный текст, включая пустой источник. |
| `{"unit":"lines","start":1,"end":2}` | Строки с 1, обе границы включены, фактические terminators сохранены. |
| `{"unit":"bytes","start":0,"end":16}` | UTF-8 bytes с 0, правая граница исключена. |

У интервального объекта ровно `unit`, `start`, `end`; границы — целые, отдельные от
`bool`, упорядочены и лежат внутри источника. Строки считаются по
`splitlines(keepends=True)`; финальный terminator не создаёт ещё одну строку.
Байтовые границы совпадают с границами UTF-8 codepoint, в том числе при пустом интервале;
пустой байтовый интервал допускается и на EOF. CRLF и полный hash исходника сохраняются.

Для фактически полученного `partial_result` и исходного `delivery_request` следующий
пример читает полный доступный остаток. Предусловие примера: его полный read-frame
помещается в явно выбранные 65536 bytes. Для большего источника выберите допустимый
интервал либо достаточный бюджет.

```python
address = next(item["address"] for item in partial_result["delivery"]["remaining"]
               if item["reason"] == "budget")
read_request = {
    "schema": "poise-portable-instruction-request-1", "operation": "read_selected",
    "selection": partial_result["observation"],
    "context": delivery_request["context"], "stage": delivery_request["stage"],
    "address": address, "range": None, "budget": {"max_bytes": 65536},
}
read_result = execute(read_request)
assert read_result["status"] == "read"
assert read_result["delivery"]["complete_material"] is True
```

Read-frame имеет ровно `schema`, `mode`, `status`, `project`, `checkout`,
`instruction_root`, `context`, `stage`, `paths`, `material`, `sha256`, `range`,
`content`, `complete_material`. Schema — `poise-portable-instructions-frame-1`,
mode/status — `read`. `material` — ключ, `sha256` — версия полного источника,
`range` повторяет допущенный интервал, `content` содержит точный выбранный текст.
`complete_material` означает полное покрытие этим одним ответом. При сборке нескольких
интервалов доступность полного текста подтверждает существующий владелец потребителя
после проверки покрытия одной версии в том же контексте.

Адрес проверяется до допуска диапазона. Если ранее адресованный источник изменился
либо стал недоступен, получите новую доставку. Она покажет актуальные остатки и действия
восстановления; после восстановления источника доступен его новый адрес. Само чтение,
receipt и наличие полного machine observation не устанавливают `retained`.

## Ошибки переносимого интерфейса

Отказ имеет ровно `schema: poise-portable-instruction-result-1`, `status: rejected`,
`error`. Python API возвращает контрактный отказ этим значением. В error ровно `code`, `field`,
`action`. При корректировке используйте
фактическое состояние источников и контекста.

| Code / field / action | Правильное продолжение |
|---|---|
| `invalid_request` / поле запроса / `provide:<field>` | Заполните точную форму запроса и типизированные значения по договору выбранной операции. |
| `invalid_selection` / `selection` / `provide:selection` | Получите полный канонический срез от владельца выбора с согласованными hash, телами и диагностикой. |
| `invalid_previous` / `previous` / `provide:previous` | Передайте актуальный полный receipt того же проекта/корней либо `previous: null` для обновления. |
| `invalid_request` / `retained` / `provide:retained` | Подтвердите уникальные ключи текущих доступных текстов, принадлежащие переданному receipt. |
| `stale_continuation` / `continuation` / `refresh:delivery` | Обновите доставку с `continuation: null` и фактическим контекстом/подтверждениями. |
| `stale_address` / `address` / `refresh:delivery` | Обновите наблюдение и доставку, восстановите указанный источник через его владельца и используйте полученный адрес. |
| `budget_too_small` / `budget` / `increase:budget` | Задайте бюджет, вмещающий обязательные метаданные навигации. |
| `budget_too_small` / `range` / `choose:range` | Выберите диапазон или бюджет, вмещающий весь read-frame и текст. |
| `invalid_range` / `range` / `choose:range` | Выберите фактические строки либо корректные UTF-8 границы внутри текущего источника. |

Файловый маршрут сохраняет [адресные отказы
подготовки](development-routing.md#отказы-входной-границы).
Недоступные источники описываются диагностикой доставки и явными остатками. При потере
источника старый read-адрес даёт `stale_address` прежде допуска чтения.

## Полный результат и агентское представление

`skills` возвращает полный JSON. Программный потребитель получает весь ответ, stdout,
stderr и исходный exit code. Агентское представление обслуживает существующий
[`tool-result`](tool-results.md#подготовка-и-вызов).

Подготовьте полную рабочую session policy и placement по
[образцу](../../config/examples/tool-results-session.example.json) и
[договору сессии](tool-results.md#сессия-и-два-сохранённых-представления).
Добавьте в `parsers` профиль `Instructions` со следующим полным описанием:

```json
{
  "parser": "poise",
  "format": "work-json",
  "brief_template": "Instructions: {status}",
  "expanded_template": "{brief}",
  "expanded_fields": [
    {"path": ["delivery"], "required": false, "format": "json", "template": "{value}"},
    {"path": ["error"], "required": false, "format": "json", "template": "{value}"}
  ],
  "expanded_separator": "\n",
  "brief": {"chars": 128, "bytes": 256},
  "expanded": {"chars": 65536, "bytes": 262144},
  "overflow": "reject"
}
```

Ключ профиля и `tool.name` запроса capture должны быть `Instructions`. Остальные параметры
заполненной policy сохраняются. Например, задайте `display.output_chars: 131072` и
`display.output_bytes: 524288`; подберите бюджеты под полный фактический пакет и внешний
envelope. Это явные значения примера, а не defaults. При нехватке бюджета общий владелец
возвращает `budget_too_small`; после успешной настройки получите полный пакет и только
тогда подтверждайте доступность текстов.

Для каждого нового вызова выберите разрешённые placement пути полных потоков и новый
capture `request_id`. Выполните producer, сохранив исходный результат:

```bash
if "${poise[@]}" skills --catalog "$skill_catalog" --root "$instruction_root" \
  < "$packet" > "$delivery_stdout" 2> "$delivery_stderr"; then
  delivery_exit=0
else
  delivery_exit=$?
fi
```

Создайте capture-пакет из полного JSON stdout, фактических путей потоков и сохранённого
`delivery_exit`. Следующий пример использует выбранный Python; каталог выходного файла
заранее подготовлен владельцем placement:

```bash
"$poise_python" -I -B - "$delivery_stdout" "$delivery_stderr" "$delivery_exit" \
  "$capture_packet" "$capture_request_id" <<'PY'
import json
import sys
from pathlib import Path

out, err, exit_code, destination, request_id = sys.argv[1:]
request = {
    "schema": "poise-tool-result-request-1", "operation": "capture",
    "request_id": request_id, "tool": {"kind": "generic", "name": "Instructions"},
    "response": json.loads(Path(out).read_text(encoding="utf-8")),
    "streams": {"stdout": out, "stderr": err},
    "capture": {"state": "complete", "reason": None}, "exit_code": int(exit_code),
}
Path(destination).write_text(json.dumps(request, ensure_ascii=False) + "\n", encoding="utf-8")
PY
"${poise[@]}" tool-result --config "$general_profile" --checkout "$checkout" \
  --policy "$tool_results_policy" --audience machine < "$capture_packet" > "$capture_result"
```

Из фактического `response_path` в `capture_result` сформируйте запрос `view` с
`view: expanded` и передайте тому же `tool-result` с `--audience agent`.
Форма запроса, в которой адрес заменяется полученным фактическим `response_path`:

```json
{
  "schema": "poise-tool-result-request-1",
  "operation": "view",
  "response_path": "/srv/runtime/results/caller/runs/current/result.json",
  "view": "expanded"
}
```

Агент получает `delivery` либо исходный `error`; полный `observation` и исходные потоки
остаются у общего владельца. Успех capture/view означает сохранение/чтение результата;
исходный exit producer сохраняется отдельным полем `exit_code`.

## Отказы и продолжение работы

В безбюджетной форме exit `0` означает `ready`; в переносимой форме —
`complete`, `partial` или `read`. В обеих формах `1` означает `needs_inputs`,
`2` — `rejected`. У rejected-ответа ровно
`schema`, `status`, `error`; `error` содержит `code`, `field`, `action`. Необязательный
`error` в parser сохраняет исходную диагностику для агента.

При недоступном обязательном источнике восстановите указанный файл через его владельца
и запросите доставку снова. Сохраните подтверждения реально доступных текстов; receipt
частичного результата перечисляет доступные источники.
При `invalid_previous` используйте актуальный receipt того же привязанного проекта
либо полное обновление с `previous: null`. При ошибке `retained` сверьте подтверждения
с receipt и доступными текстами. Неизвестный навык этапа согласуйте с фактическим
назначением и актуальным каталогом. Остальные адресные действия источника, профиля и
policy сохраняют [обычного владельца подготовки](development-routing.md#отказы-входной-границы).

## Границы ответственности

Skills выбирает материалы, наблюдает байты и формирует семантическую доставку. Приложение
координирует одно наблюдение через существующий порт; adapter проверяет общий профиль,
Git-привязку и читает источники. Receipt — передаваемое значение; новой БД или хранилища
доступности и автоматического восстановления конфигурации нет.

Запрошенные файлы кода, тестов, правил и документации читатель возвращает целиком либо
в запрошенном диапазоне согласно своему договору допуска. Существующие filesystem-операции чтения
сохраняют свой договор; `read_selected`
выбирает текст текущего наблюдения через общий чистый владелец диапазонов.
Обычная механическая сводка контекста остаётся сведениями своего владельца. Доставка
использует явные данные текущего контекста и подтверждения вызывающей стороны.
Проверяемая граница — наш API и выбранный потребитель, без утверждений о запросах OpenAI,
серверной доставке или понимании текста моделью.
