# Markdown formatter fallback AI poise

Обновлено: **2026-09-15**, C027.4.

## Явное форматирование при недоступной IDE

Предпочтителен применимый IDE-owned formatter. Когда он не выдан, не привязан к
нужному файлу или неприменим, причина указывается явно в `--fallback-reason`.
Fallback `tools/format_markdown.py` работает с уже установленным Poise, используя
один `format_files`/`format_markdown`. Не выбирает executable из проверяемой копии.

Пример из папки AI-poise с установленным пакетом:

```bash
python tools/format_markdown.py --checkout . --files docs/workflows/markdown-formatting.md \
  --len 100 --eol LF --lint-mode fix --fallback-reason 'IDE formatter is unavailable'
```

`--file` повторяется; `--files` принимает список; `--files-from FILE` либо `-` читает
**явно заданный список** путей, но не сканирует каталог. Пути файлов разрешаются
обычным способом относительно cwd вызывающего процесса; absolute paths поддержаны.
`--checkout` ограничивает область файлами собственной копии AI-poise, не задаёт
глобальное разрешение инструментов. Нельзя форматировать чужой проект, path escape,
symlink, каталог или не-Markdown файл. До первого изменения проверяется весь список.

`check` не пишет файлы; возвращает exit 1 при отличии канонического результата.
`fix` сначала вычисляет результат и пишет **только отличающийся** файл. Повторный fix
не меняет inode/mtime уже отформатированного файла. Exit 0 — успех, exit 2 — неверные
аргументы/файлы либо I/O failure. После fix отдельный check не обязателен.
Режим, длина и EOL обязательны: нет скрытого перехода к мутации или project-wide policy.

## Сохранённый контракт и ограничения

Основа — ERP `ai-assistant/md-fmt.py`, включая MDFMT-001/002: line wrapping, continuation
indent списков, пропуск headings/tables, fenced code и начального front matter,
сохранение наличия финального EOL. LF/CR/CRLF сравниваются по исходным байтам, без
неявной universal-newline нормализации. Fence закрывается только маркером того же
символа достаточной длины; hardbreak spaces сохраняются. Indented code и blockquote
не reflow-ятся без структурного parser. Длинные неразделимые tokens не разрываются.

Это консервативный line formatter, не полный CommonMark parser и не ERP style gate.
Он не объединяет абзацы, не форматирует код внутри fences и не переписывает таблицы.
EOL меняется по явному выбору и внутри сохранённых blocks. Formatting — не проверка
ссылок: ссылки проверяет прежний [documentation owner](documentation-checks.md#проверка-ссылок-и-точных-заголовков).

Замена каждого изменённого файла атомарна через собственный temporary file рядом с
ним, с сохранением permissions. Проверяется drift перед заменой. Весь multi-file
пакет не объявляется filesystem-транзакцией: при позднем I/O failure ошибка перечисляет
уже заменённые файлы. Исправления не распространяются на невыбранные документы.
Native Windows path rewriting и ERP shell/Composer wrappers не переносились.
