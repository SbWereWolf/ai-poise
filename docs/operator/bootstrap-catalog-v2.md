# Bootstrap и каталог catalog/v2

Обновлено: 2026-09-28. Это руководство относится к подготовленному корневому
`bootstrap` AI poise, а не к действующему маршруту `poise infra/deps/check` с
профилем `requirements/v1`. Подготовка Bootstrap сама не переключает установленного
оператора. Эксплуатационный каталог и выбор профиля для AI poise остаются за
отдельной поставкой P002.

## Граница приложений и происхождение

AI poise владеет только корневым `bootstrap`, транспортным адаптером
`apps/bootstrap/application.py` и lock-файлом `apps/bootstrap/component.json`.
Отдельное приложение `environment-maintenance` владеет schema
`environment-maintenance/catalog/v2`, параметрами, разрешённым окружением,
выполнением действий, состояниями и повторной проверкой. Его исходники и generic
engine не копируются в AI poise.

Lock фиксирует `schema=ai-poise/bootstrap-component/v1`, дистрибутив
`environment-maintenance`, версию `0.2.0`, принятый commit
`a980fcd54d0958e0a491efb385ede406453d6899`, SHA-256 сохранённого wheel
`6b96c53b1e189665e4922252ee1b80b2bef9c64ceec214dc557884bec4fac5fc`
и SHA-256 delivery manifest
`3bfab74a2d5c38a6e09880b9aa4e45437a0b119f0cdda56b1de0946fe68e029a`.
Эти digest проверяются при подготовке поставки по сохранённым файлам.
При запуске Bootstrap проверяет lock, установленную версию дистрибутива и
доступность модуля в том же изолированном Python, который выполнит действие.
Проверка установленной версии не восстанавливает digest исходного wheel.

## Подготовка и безопасная проверка

Нужен Python 3.13 с установленным ровно `environment-maintenance` 0.2.0.
Оператор заранее предоставляет wheel и каталог v2 с `catalog.json`; Bootstrap
не выбирает каталог по умолчанию. Следующие пути предполагают существующий
родитель `/srv/poise` и новую отдельную venv; это пример площадки, а не
встроенные значения AI poise:

```bash
BOOTSTRAP_ROOT=/srv/poise/release
BOOTSTRAP_PYTHON=/srv/poise/bootstrap-venv/bin/python
CATALOG_ROOT=/srv/poise/catalog-v2

sha256sum /srv/poise/packages/environment_maintenance-0.2.0-py3-none-any.whl
sha256sum /srv/poise/packages/delivery.json
python3.13 -m venv /srv/poise/bootstrap-venv
"$BOOTSTRAP_PYTHON" -m pip install --no-index --no-deps \
  /srv/poise/packages/environment_maintenance-0.2.0-py3-none-any.whl

"$BOOTSTRAP_PYTHON" -I -B "$BOOTSTRAP_ROOT/bootstrap" check \
  --catalog-dir "$CATALOG_ROOT"
"$BOOTSTRAP_PYTHON" -I -B "$BOOTSTRAP_ROOT/bootstrap" infra \
  --catalog-dir "$CATALOG_ROOT" --dry-run
"$BOOTSTRAP_PYTHON" -I -B "$BOOTSTRAP_ROOT/bootstrap" deps \
  --catalog-dir "$CATALOG_ROOT" --dry-run
```

Сравните оба выведенных SHA-256 с lock до установки wheel и применения
действий. Wheel должен быть установлен именно в `BOOTSTRAP_PYTHON`; одного
файла на диске недостаточно. `bootstrap` запускается вместе с соседним каталогом
`apps/bootstrap/` из той же проверенной копии AI poise. Корневой entrypoint,
`application.py` и `component.json` поставляются вместе; `POISE_CONFIG`, Task
и сессия harness самому Bootstrap не нужны.

После выбора настоящего каталога `infra` и `deps` разрешены как изменяющие
операции. Оба имени передаются внешнему приложению как `run`; различие задаёт
только явно выбранный каталог. `check` передаётся как `check`.
`infra/deps --dry-run` передаются как `run --dry-run`.
Bootstrap сохраняет порядок всех аргументов после публичного имени, включая
повторяемые `--values` и `--set`. `check` и `--dry-run` не вызывают `install` или
`repair`. Секреты не следует передавать через `--set`: argv доступен системе.

## Результаты и ошибки

После успешного preflight Bootstrap запускает один процесс
`python -I -B -m environment_maintenance` с отображённой командой. Для обычного
завершения он передаёт stdout, stderr и код выхода без разбора или замены JSON.
Внешний результат имеет schema `environment-maintenance/result/v2`: код `0`
означает `ready`, `2` — `action_required` либо ошибку входа, `3` — `unavailable`,
`4` — `failed`. Ошибка входа внешнего приложения печатается в stderr. Для
`missing` обычный `run` выбирает `install`, для `invalid` — `repair`; после каждой
попытки изменения, включая неуспешную, внешний компонент повторяет `check`.
`unavailable` содержит причину и рекомендации без изменения среды.

Ошибка, созданная самим Bootstrap после разбора публичной команды, выводится
одним компактным UTF-8 JSON-объектом и завершающим LF в stdout, без stderr.
Её отдельная schema — `ai-poise/bootstrap-error/v1`. Поля:
`schema`, `source=bootstrap`, `status=error`, `code`, `command`, непустая `cause`,
непустой массив `recommendations`, `exit_code`. `command` — полученное публичное
имя `infra`, `deps` или `check`. Bootstrap не выдаёт эту ошибку за внешний
`environment-maintenance/result/v2`.

| Ситуация | `code` Bootstrap | Код выхода | Действие оператора |
|---|---|---:|---|
| Lock отсутствует, недоступен или повреждён | `component_lock_missing`, `component_lock_unreadable`, `component_lock_invalid` | 2 | Восстановить проверенный lock из той же поставки; не исправлять его поля наугад. |
| Дистрибутив или модуль не виден изолированному Python | `component_missing` | 3 | Установить принятый wheel в выбранный интерпретатор и повторить безопасный `check`. |
| Установленная версия отличается от lock | `component_version_mismatch` | 3 | Установить ровно указанную в lock версию. |
| Не запустился probe/action или probe завершился неожиданно | `component_spawn_failed` | 4 | Проверить интерпретатор и установку компонента; сохранить диагностику до повтора. |
| Action завершился сигналом | `component_signaled` | 4 | Изучить номер сигнала в `cause` и последствия действия до нового изменяющего запуска. |

Неизвестная публичная команда, отсутствующий или пустой `--catalog-dir` отклоняются
`argparse` до probe/action: код `2`, сообщение в stderr, без Bootstrap JSON.
Повреждённый lock, включая повторные/неизвестные поля, неверный UTF-8,
экранированный NUL, одиночный surrogate и чрезмерную глубину JSON, не запускает
компонент. Ошибки каталога, параметров, действия и протокола принадлежат внешнему
приложению; проверяйте его `cause`, `recommendations`, `invocation`, `error`,
`timed_out`, `capture_complete` и сохранённые потоки. Bootstrap их не исправляет
и автоматически не повторяет изменяющие действия.

## Откат и пределы поставки

Само подключение Bootstrap не меняет текущий release pointer оператора,
проектный конфиг или базы. Явно выбранное действие `infra/deps` может изменить
окружение по каталогу. Для отказа от пути прекратите его вызовы и сохраните выбранные
каталог, lock, вывод и код выхода. Возврат к прежней установленной поставке
проводится её отдельным управляемым lifecycle; Bootstrap не выполняет миграцию
профиля v1 и не удаляет результаты уже совершённых `install` или `repair`.
После частичного изменения сначала диагностируйте состояние по каталогу и
рекомендациям компонента, затем примите отдельное решение о восстановлении.

Проверены архитектурные тесты публичной границы, отдельного компонента и
структурированных отказов (24 успешных случая без пропусков), а также bounded
smoke (6 успешных проверок) на подготовленной ветке. Это не доказательство
переключения эксплуатационного оператора или существования каталога P002.
