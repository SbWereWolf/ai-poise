# Внешние основания DDD-07C

Проверено **2026-09-07**. Первичные источники; описанное внешнее поведение не доказано локальным стендом.

- OpenAI Codex Hooks: https://developers.openai.com/codex/hooks/ (перенаправляет на https://learn.chatgpt.com/docs/hooks). Проверены hooks.json, command stdin JSON, SessionStart/UserPromptSubmit/Stop/SessionEnd, trust, bounded context и различие блокировки Stop от уведомления. Не реализуются/не используются deprecated aliases и trust bypass.
- JetBrains MCP: https://www.jetbrains.com/help/idea/mcp-server.html . Copy Stdio Config, projectPath, exposed tools. Инвентарь зависит от реально включённых tools; код не предполагает обязательное наличие конкретного имени без tools/list.
- MCP transport: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports . STDIO JSON-RPC, newline frames, protocol stdout vs diagnostic stderr.
- MCP lifecycle: https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle . initialize и initialized, согласованная версия.

Запуск проб реального Python/SQLite/Git в текущем контейнере записывается отдельно. Codex executable в текущем PATH не обнаружен; живой endpoint IDE не предоставлен. Hooks install/replay не выдаётся за успешный запуск hook внутри Codex UI.
