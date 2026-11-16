# Инструменты рефакторинга Poise

Poise имеет собственную установку в `tools/refactoring`, отдельно от
приложений ERP. Состав и команды запуска заданы в
[toolchain.json](../../tools/refactoring/toolchain.json), список конфигов
установки — в [applications.json](../../tools/refactoring/applications.json).
Версии npm закреплены lock-файлом, Python — `requirements.txt`.

Минимальный набор: ast-grep, jscpd, Ruff, Radon, Import Linter, клиенты
SonarQube и CodeScene. Локальные зависимости, бинарные файлы и кеши
исключены из Git. CodeScene закреплён ревизией и SHA-256 дистрибутива.

## Штатная установка

Используйте выбранный для Poise исполняемый файл Environment Maintenance
и профиль [start-app.json](../../tools/refactoring/start-app.json).
Передайте реальный файл параметров по
[примеру](../../tools/refactoring/parameters.example.json), с корнем
checkout, выбранными исполняемыми файлами и явным PATH:

```bash
"$POISE_START_APP" deps \
  --config tools/refactoring/start-app.json \
  --parameters "$REFACTORING_PARAMETERS" \
  --base-dir "$POISE_ROOT" \
  --requirements '["refactoring-tools"]'
```

Операция устанавливает закреплённые зависимости в Poise и проверяет
запуск каждого инструмента. Ошибка установки, идентичности дистрибутива
или запуска остаётся ошибкой операции.

## Граница результата

Минимальные конфиги и успешный запуск не доказывают качество исходников
или выполненный рефакторинг. AST-правила и архитектурные ограничения
требуют отдельной настройки. Для SonarQube и CodeScene нужны явно
выбранные сервис, проект, правила и разрешённые учётные данные. До
фактического анализа метрики недоступны; нулевые значения не подставляют.

Каждая пара «инструмент — Poise» имеет отдельную задачу полной настройки;
идентификаторы хранятся в `toolchain.json` после создания задач в Poise.
Полная настройка включает область исходников, правила и исключения,
сырые отчёты, идентичность версий/конфигов/исходников и приёмку результата.

Цель Twill и единственный домашний комплект задаёт
[task-goal-routing.json](../../config/development/task-goal-routing.json).
Соответствие приложения и планируемых путей локальным навыкам задаёт
[skill-selection.json](../../config/development/skill-selection.json).
Внутренние навыки вызывают только явно. Замороженные задачи сохраняют
собственный договор.
