# Bootstrap standalone shell

`PS-DEV-002-T022` выделяет самостоятельную process/config boundary Bootstrap. Полный multi-target контракт остаётся в [contract-bst.md](contract-bst.md); этот файл описывает только уже реализованный shell foundation.

## Команда

Публичный executable задачи:

```text
apps/bootstrap/bin/bootstrap --config PATH check
apps/bootstrap/bin/bootstrap --config PATH infra [--target TARGET] [--dry-run] [--set NAME=VALUE ...]
apps/bootstrap/bin/bootstrap --config PATH deps  [--target TARGET] [--dry-run] [--set NAME=VALUE ...]
```

Shell не использует общий `poise work` Runtime, `POISE_CONFIG` или скрытый install path. Неизвестная команда/flag, duplicate `--target`, отсутствие значения и malformed selector завершаются fail-closed JSON rejection и exit 2; stderr не используется для нормального protocol result.

## Конфигурация и ownership

`--config` обязателен. Файл имеет exact schema `stride-bootstrap-config/v1`:

- `projects_root` — Bootstrap-owned operational root, абсолютный либо разрешаемый относительно каталога config;
- `default_target` — configured selector (`all` либо допустимый target ID);
- `target_env` — явное имя environment variable либо `null`; при `null` никакого скрытого env default нет;
- `limits` — обязательные `max_targets`, `max_target_bytes`, `max_output_bytes`, `target_timeout_seconds`, `run_timeout_seconds`.

Unknown/missing fields, неверная schema, non-positive/non-finite limits и неверные selector values дают `invalid_bootstrap_config` до side effects. Отсутствующий configured `projects_root` даёт `projects_unavailable`.

`projects_root` принадлежит Bootstrap. Shell не читает private database/config Requirements, Sequencing, Execution или других приложений и не создаёт чужое canonical state. В текущем T022 slice пустой существующий root для `check` возвращает `stride-bootstrap/v1` / `not_required`; это доказывает самостоятельную entry/config boundary без подмены дальнейшего multi-target engine.

## Граница текущей задачи

T022 намеренно **не** реализует полную Bootstrap semantics из `contract-bst.md`:

- **T026** добавляет dynamic local `projects/` target discovery/selection и CLI > env precedence;
- **T027** добавляет maintenance `check/apply/check` execution;
- **T028** добавляет публичные repair adapters вместо private DB/domain access.

Поэтому наличие target records в текущем foundation не объявляется готовым maintenance run. Эти операции не должны появляться скрыто раньше своих owner Tasks.

## Проверка

Behavior contract:

```text
python -B apps/bootstrap/tests/test_shell.py --suite behavior
```

Документационный contract:

```text
python -B apps/bootstrap/tests/test_shell.py --suite documentation
```

Behavior suite проверяет executable `apps/bootstrap/bin/bootstrap`, два независимых config/projects roots, отсутствие чтения соседних private stores, fail-closed required/unknown config, configured limits, unavailable root, CLI grammar и отсутствие скрытого target environment default.
