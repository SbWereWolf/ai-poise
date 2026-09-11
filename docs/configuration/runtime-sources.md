# Источники для runtime-среза

Проверено: **2026-09-07T00:07:53+05:00**. Это основания выбора механизмов, не доказательства запуска конкретной пользовательской установки.

- OpenAI, hooks: https://developers.openai.com/codex/hooks/ (перенаправление на https://learn.chatgpt.com/docs/hooks). Реальные события и поля зависят от runtime; поэтому пакет сохраняет explicit source и не обещает доступ CLI к внутренним tools. В этом срезе hook config не устанавливается в Codex.
- Python 3.13, subprocess: https://docs.python.org/3.13/library/subprocess.html . Рабочие процессы materialize используют Popen, отдельную process group и явное ожидание/timeout.
- Git bundle: https://git-scm.com/docs/git-bundle . Bundle применяется как проверяемая локальная копия Git objects/refs; он не является snapshot SQLite или внешним backup.

Форма event_msg/user_message проверена в тестовых JSONL fixtures, составленных по ранее наблюдавшимся журналам проекта. Это узкий versioned adapter, не обещание распознать любой старый/будущий rollout. Доступ к реальному Codex/JetBrains/Gmail этой сессией не проверялся.
