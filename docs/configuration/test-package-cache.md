# Кэш тестовых пакетов AI poise

Этот механизм существует только для тестовых пакетов собственной кодовой базы AI poise. Он не является адаптером кэша для репозиториев, которыми Poise помогает управлять.

## Расположение

Для конкретной проверяемой копии AI poise кэш хранится в `.poise-test-cache/` **в этой копии**. Все пути в records/evidence сохраняются относительно её filesystem path. Перенос или отдельный worktree другой кодовой базы не перенаправляет этот кэш в mutable state Poise.

## Идентичность кэша

Fingerprint строится только из объявленного C016 membership и SHA-256 содержимого четырёх классов входов: `source`, `tests`, `support`, `fixtures`. Пути нормализуются относительно начала проверяемой копии AI poise.

В fingerprint намеренно не входят environment, Python/runtime version, runner configuration, timeout policy, lock-файлы сами по себе или абсолютное расположение checkout. Файл влияет на fingerprint только если он включён в membership конкретного пакета. Это принятая семантика C018, а не универсальное правило для чужих проектов.

## Reuse и provenance

Успешный fresh run сохраняет terminal evidence и cache record с `package_id`, `input_fingerprint`, `origin_run_id`, относительным `evidence_path` и digest evidence. Cache hit не создаёт вымышленный новый запуск: результат возвращается с `reused=true`, `executed=false` и тем же `origin_run_id`.

Повреждённый/исчезнувший origin evidence является ошибкой, а не cache hit. Неуспешный или прерванный запуск evidence сохраняет, но reusable record не создаёт.

`--fresh` в `tools/run_ai_poise_test_package.py` явно обходит reuse и выполняет пакет повторно; успешный новый запуск становится новым origin для того же content fingerprint.
