# Конфигурация

## Материалы

- [catalogue-stage-map.md](catalogue-stage-map.md)
- [check-runner-timeouts.md](check-runner-timeouts.md)
- [named-project-checks.md](named-project-checks.md)
- [development-routing.md](development-routing.md)
- [goal-config.md](goal-config.md)
- [local-assets.md](local-assets.md)
- [workflow-catalogue.md](workflow-catalogue.md)
- [project-setup.md](project-setup.md)
- [runtime-hooks.md](runtime-hooks.md)
- [runtime-services.md](runtime-services.md)
- [runtime-sources.md](runtime-sources.md)
- [skill-catalog.md](skill-catalog.md)
- [storage-layout.md](storage-layout.md)
- [test-package-cache.md](test-package-cache.md)
- [test-packages.md](test-packages.md)
- [wsl-local-delivery.md](wsl-local-delivery.md)

- [project-file-reading.md](project-file-reading.md) — общий reader, обе политики, операции, рамки и ошибки.
- [Жизненный цикл reader](../workflows/file-intake-lifecycle.md) — шаблон, native admission и восстановление.

- [Учёт обычных команд](project-file-reading.md#подключение-учёта-обычных-команд) — полный
  command-enabled комплект, справочник всех options и совместная работа с прямым reader.
- [Подключение tool events](runtime-hooks.md#подключение-событий-чтения) — observed revision,
  immutable definition, admission и настоящий native lifecycle.
- [Coverage](project-file-reading.md#сводка-покрытия) — восьмая операция при включённом
  профиле, bounded metadata и ошибки/продолжение.
