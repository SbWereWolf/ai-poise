# Стартовый Sprint Harness

Этот каталог содержит task contracts для материализации публичным API Harness. Файлы JSON — поставочные определения, а не альтернативное постоянное task storage.

После окончательной настройки WSL-путей `tools/seed_wsl_tasks.py` создаёт project-local `tasks.sqlite` и один `SPRINT-0001`:

- `0001` — Requirements DB + requirement traceability;
- `0002` — IDE MCP discovery in preflight;
- `0003` — JetBrains MCP research and agent policy.

Зависимости: `0001 → 0002 → 0003`, `kind=result`.

`requirements-bootstrap.json` содержит согласованные future требования первого bootstrap. После реализации `0001` они импортируются в project-local Requirements DB и bootstrap-файл не остаётся каноническим источником.
