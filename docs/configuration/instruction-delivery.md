# Доставка инструкций в текущий контекст

Обновлено: **2026-11-03**.

`skills/deliver` получает инструкции для выбранных файлов, навыков этапа и одного
инструмента. Полное наблюдение сохраняется для машинного потребителя; отдельный пакет
доставки повторяет тексты, необходимые текущему контексту. Операция `prepare` сохраняет
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

stdin содержит ровно десять обязательных полей; `previous` и `tool` допускают `null`.

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

Ответ содержит ровно `schema`, `status`, `observation`, `delivery`, `receipt`.
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
его навыков, context id или поколения приводит к полной текущей доставке.
При фактическом resume передайте `reason: resume`, после сжатия — `reason: compaction`;
оба значения требуют полной доставки независимо от исторического receipt.
Новый контекст получает новую метку/поколение и `reason: initial`.

При неизвестной доступности используйте пустой `retained`. При отсутствии пригодного
receipt используйте `previous: null`: результатом будет полная текущая доставка,
включая случай уже переданных корректно типизированных подтверждений. При существующем
receipt подтверждаемые ключи принадлежат ему. История чтения и неизменный hash сами
по себе не заменяют текущего подтверждения.

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

Exit `0` означает `ready`, `1` — `needs_inputs`, `2` — `rejected`. У rejected-ответа ровно
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
в запрошенном диапазоне согласно своему договору допуска. Этот API чтения не изменён.
Обычная механическая сводка контекста остаётся сведениями своего владельца. Доставка
использует явные данные текущего контекста и подтверждения вызывающей стороны.
Проверяемая граница — наш API и выбранный потребитель, без утверждений о запросах OpenAI,
серверной доставке или понимании текста моделью.
