# Технические основания реализации DDD-04

Обновлено: **2026-09-06T19:15:45+05:00**. Срез **HARNESS-DDD-04**.

Обращение к официальной документации выполнено в ходе среза. Внешние источники используются для механики Python; правила процесса взяты из пользовательских требований и архитектуры проекта.

- Python 3.13 sqlite3: https://docs.python.org/3.13/library/sqlite3.html — явные транзакции, FK включается на соединении, autocommit не заменяет domain/UoW.
- Python 3.13 subprocess: https://docs.python.org/3.13/library/subprocess.html — wait/timeout/returncode, отрицательный returncode сигнала не бизнес-вердикт проверки.

Никакие OpenAI API возможности/usage/hooks не заявляются проверенными этим срезом. Runtime совместимость фактически проверялась только данным Linux/Python/Git sandbox и локальными remotes.
