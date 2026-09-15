# Локальные ссылки и anchors документации AI poise

## Проверка ссылок и точных заголовков

При изменении документа, заголовка или правила со ссылкой проверяйте и исходящий текст,
и ссылки из неизменённых документов на изменённый/удалённый target. Ссылку из AGENTS
на нормативное правило задавайте точным `файл.md#заголовок`, не ссылкой на всю документацию.
Используйте существующего владельца `poise.infrastructure.documentation_checks` через
`tools/check_documentation.py` или owning tests; не добавляйте параллельный checker.

Эта проверка обслуживает **документацию самой AI poise**. Она не меняет расположение или
разрешение файлов запущенных hooks/tools и не навязывает правила другим репозиториям.
Аргумент `--checkout` — конкретная проверяемая папка; код проверяющего инструмента берётся
из уже установленного Poise. Проверка кандидатного кода в test environment не переключает
live installation.

```sh
python /path/to/ai-poise/tools/check_documentation.py --checkout /path/to/checked-ai-poise
python /path/to/ai-poise/tools/check_documentation.py \
  --checkout /path/to/checked-ai-poise \
  --changed docs/workflows/sprints.md --changed docs/removed-page.md
```

`--changed` принимает повторяемые относительные/абсолютные filesystem paths, в том числе
уже удалённые файлы и каталоги. Checker читает текущий набор Markdown для обнаружения
**входящих** ссылок и оставляет диагностику, если выбран source либо target. Изменённая
страница не обязана быть источником сломанной ссылки. За пределы проверяемой папки
проверка не выходит. Она не редактирует файлы и не выполняет ссылки/команды.

Результат: `valid` / exit 0; ошибки локальных links/anchors — `invalid` / exit 1;
неверные параметры/недоступный ввод — `rejected` / exit 2. `scanned_documents` показывает
весь прочитанный набор, а не только отфильтрованные diagnostics. Исходный allowlist
`config/legacy-namespace-allowlist.json` исключает лишь явно исторические материалы;
проверяемый набор не содержит `.git`, dependency environments и test caches.

## Поддержанный синтаксис

Поддержаны inline/image links, reference-style labels (полная, collapsed, объявленная
shortcut-форма), percent-encoded локальные пути, angle destinations с пробелами, вложенные
скобки в destination, относительные пути и `#fragment` того же документа. Примеры синтаксиса:

```markdown
[Политика](sprints.md#замкнутость-зависимостей-и-локальные-дубли)
[С пробелом](<page name.md#heading>)
[Индекс][policy]
[policy]: sprints.md#замкнутость-зависимостей-и-локальные-дубли "Политика"
```

Anchors строятся для обычных ATX (`#` … `######`) и Setext-заголовков; сохраняются буквы
Unicode, цифры, дефисы и подчёркивания, регистр приводится к нижнему, пробелы заменяются
дефисами. Убираются markup и пунктуация, повторения получают `-1`, `-2`. HTML entities
декодируются; явные HTML `id`/`name` в кавычках также доступны как targets. Fenced/indented
code, front matter, HTML comments не создают заголовков. Вложенные списки не принимаются
за indented code, если это продолжение пункта.

Это проверяемый локальный subset, **не полная реализация CommonMark и не обещание одинаковой
генерации ID всеми renderer extensions**. Не поддерживаются template/MDX expansion,
неявные HTML-generated headings, автоматически созданные footnotes, HTML `href` и
renderer-specific атрибуты `{#id}`. Для специального target используйте явный HTML `id`
или добавляйте отдельно согласованный пример и тест в того же владельца.

Внешние URLs, root-relative web links и fragments не-Markdown-файлов перечисляются в
`skipped`, без сетевого доступа. Успешный локальный аудит **не подтверждает** работоспособность
этих ссылок, истинность текста или исполнимость примеров команд. ERP line-width/formatting
policy не переносится.

## Owning tests и воспроизведение

`tests/delivery/test_documentation_checks.py` проверяет anchors, их исчезновение,
изменённые/удалённые targets, supported syntax и точные ссылки AGENTS на migration,
Sprint и checkpoint правила. Прежняя delivery-проверка
`test_current_documentation_links_and_commands_use_poise` использует тот же owner.
Пакет `documentation-links` находится в существующем C016 test catalogue и запускается
обычным registered runner. Его cache inputs явно включают соответствующие Markdown
и allowlist. [Skill-reference audit](tooling-checks.md#структура-и-достижимость-skill-references)
использует того же владельца ссылок, но имеет отдельную задачу достижимости и иной отчёт.
