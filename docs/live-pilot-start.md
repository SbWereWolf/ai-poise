# Следующий живой пилот

Обновлено: 2026-09-09T01:10:16+05:00.

Локальный пилот завершён. Нужны **выбранный проект и конкретное поручение**, а не ещё один
абстрактный осмотр ТЗ. Можно предоставить уже созданный project config и доступный Git-архив;
физические пути принимающей среды назначаются штатным setup. Не передавать секреты в тексте
задачи или через публичные URL.

## Подготовка проекта в доступном WSL / Linux

Из корня распакованного Harness, при установленном Python 3.13 и Git:

```bash
python -c 'import sys,sqlite3; assert sys.version_info[:2] == (3,13); print(sys.version); print(sqlite3.sqlite_version)'
git --version
export PYTHONPATH="$PWD/src"
python -m harness project-init \
  --settings "$PWD/config/project-setup.json" \
  --template linux-reference \
  --destination config/projects/live-pilot-new \
  --request-id LIVE-PILOT-SETUP-1 \
  --probe-repository yes
```

Каталог назначения должен быть новым. В анкете явно задаются repository, base ref, автор,
remote/push, путь state и окружение. Не надо вручную составлять или редактировать config.
Сохранить возвращённый config_path. Для другого пути/повтора выбирать осознанно новое
назначение и request_id; готовые рабочие конфиги не удалять.

## Что требуется для native-проверки

Запустить проверки из docs/runtime-hooks.md в настоящей Codex/WSL среде. Проверка отсутствия
codex в контейнере ChatGPT не описывает ваш компьютер. Для IDE нужен реальный настроенный
MCP endpoint или stdio bridge и проверяемый project/worktree binding. Не открывать публичный
endpoint и не передавать credentials ради пилота. Gmail не обязателен для локального пилота;
удалённый backup требует выбранного адресата/интеграции.

## Как продолжить сохранённое, а не создавать историю заново

Окончательный transfer package лежит в evidence/autonomous-pilot/checkpoints/completed.zip
полного комплекта. Он имеет формат harness-transfer-1 и SQLite schema 12. Import возможен
в пустом/новом store с project=harness-pilot и тем же execution contract. Пакет не мигрирует
данные и не сливает существующего владельца. При необходимости прочитать прежнюю пилотную
задачу использовать import → bootstrap → show; она уже completed, а не снова ready/planning.
