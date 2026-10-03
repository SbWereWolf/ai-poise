# Общая ограниченная граница SQLite-записи

Решение `REVIEW-ARCH-A05` для R03. Реализовано в `REVIEW-BUG-R03` 18 сентября 2026 года; схемы и
конфигурационные пути не изменены.
База `101304f58aca34a1104d2cdcc27d6434e5f9fca8`.

## Основание

[RequirementsStore._transaction](../../src/poise/infrastructure/requirements_registry.py)
берёт application flock, открывает SQLite с timeout=0, затем deferred BEGIN. Независимый
SQLite writer не обязан брать тот же lock-файл. Поэтому первое чтение может пройти,
а DML — немедленно завершиться database is locked. Исходный опыт —
`../../projects/ai-poise/standalone/REVIEW-PLAN-20260918/artifacts/source-review/evidence/R03-requirements-contention.json`
(исторический артефакт; не включён в исходную поставку 044A)
фиксирует такой отказ за ~0.00032 s при writer hold 0.15 s и lock_seconds=1.0.
Повреждение БД в этом опыте не показано; нужен bounded wait, не восстановление утраченных
данных. Task Database уже использует общую правильную границу.

## Решение и отображение параметров

RequirementsStore._transaction делегирует существующему
[write_transaction](../../src/poise/infrastructure/sqlite/transaction.py), не копирует
его реализацию и не создаёт собственный retry-декоратор.

| RequirementsStore | Общая граница | Семантика |
|---|---|---|
| `self.database` | `path` | Точный настроенный файл Requirements DB, не Task DB. |
| `self.lock` | `lock` | Существующий application lock данной установки. |
| `self.lock_seconds` | `wait` | Конечный бюджет **каждого** ожидания flock, BEGIN и COMMIT. |
| `self.poll_seconds` | `poll` | Конечный шаг ожидания, ограниченный остатком monotonic deadline. |

Последовательность: flock → connect(timeout=0, explicit autocommit) → FK ON/verify →
BEGIN IMMEDIATE с bounded retry → **однократное** тело → COMMIT с bounded retry → close.
Write reservation приобретается до чтения request digest/revision/registry. Это
устраняет deferred read-to-write upgrade в операции apply. Тело, в частности
RequirementsRegistry.apply и DELETE/INSERT registry, не повторяется при COMMIT BUSY.
Каждая SQL boundary имеет monotonic deadline; это не общий секундомер всего запроса.
В худшем случае он включает последовательные бюджеты нескольких ожиданий и время
однократного тела. Документация не должна называть lock_seconds end-to-end timeout.

Допустимый retry — только SQLITE_BUSY именно на BEGIN IMMEDIATE и COMMIT. SQLITE_LOCKED,
BUSY_SNAPSHOT, I/O corruption и произвольные OperationalError не превращаются в цикл
повтора. Сопоставляется sqlite_errorcode, не строка сообщения. При исчерпании бюджета
COMMIT общий helper откатывает открытую транзакцию; диагностическая ошибка сохраняет
файл/операцию и запрещает удалять БД/lock как способ лечения. При ошибке rollback
первичная ошибка остаётся доступной. Числовые параметры проверяются как finite positive.

## Неизменяемые контракты

Expected revision проверяется после reservation; при чужом commit возвращается
VersionConflict, не перезаписываются более новые требования. Точный request_id+digest
возвращает сохранённый response с replayed=true без изменения revision. Тот же ID с
другим digest отвергается. Требования/связи/response сохраняются одной транзакцией;
FK и domain validation не ослабляются. Имена файлов, host path mapping, Requirements
schema=1 и Task schema=13 сохраняются. Это **общий механизм, не общая база**.

Initialization использует ту же границу: две конкурирующие инициализации не создают
частичную schema, вторая видит завершённую schema1. Неизвестная непустая база и чужая
версия отклоняются без автоматической миграции. TaskRequirementsSnapshotStore остаётся
изолированным тестовым adapter; его нельзя направлять на versioned Task DB. Read методы
RequirementsStore на этом срезе также сохраняют существующий serialized transaction
контракт; предлагаемое разделение read snapshots из A02 сюда не внедряется скрыто.

## Failure matrix и проверка R03

| Сценарий | Ожидаемый инвариант |
|---|---|
| Независимый writer отпускает за 0.15 s при budget1 s | Apply дожидается, revision увеличена один раз, receipt request один. |
| Writer держит дольше маленького явного бюджета | Контролируемая ошибка в пределах бюджета+погрешности, исходные rows/revision/request IDs неизменны. |
| COMMIT блокирует короткий читатель DELETE-журнала | Повторяется только COMMIT; счётчик исполнения domain apply=1. |
| COMMIT блокирован сверх бюджета | Полный rollback, version/links/request response не опубликованы. |
| Revision устарела во время ожидания | VersionConflict после acquisition, без потери чужой записи. |
| Точный replay / reused ID с другим digest | Первый не меняет registry/revision; второй отвергается. |
| Domain exception/SQL constraint внутри тела | Полный rollback, FK/integrity остаются корректными. |
| Две инициализации и независимый SQLite writer | Одна целая schema, обе совместимые операции проходят после bounded wait; неизвестная schema не меняется. |

Все опыты только во временных SQLite-файлах с короткими удержаниями и Events для
синхронизации; не использовать большие sleeps как замену подтверждения lock. Сохранить
исходный R03 RED и новый GREEN, соседние requirements registry/bootstrap/replay и общие
transaction tests. Для измерений total elapsed учитывать отдельность стадий ожидания.

## Альтернативы и ввод

Повысить sqlite connect timeout недостаточно для точного различения boundary retry и
read-to-write upgrade; новый blanket retry ещё хуже — повторяет тело. Удаление flock
ломает существующую сериализацию сотрудничающих владельцев. Объединение Requirements
с Task DB не требуется для устранения дефекта и меняет их независимые обязанности.

Ввод — маленькая замена делегирования плюс RED/GREEN tests; нет schema migration,
данные пользователя не переписываются. Откат возвращает старый immediate-failure
риск, но не требует преобразования файлов. До любых дальнейших изменений схемы —
отдельный проект и проверенная контрольная точка по
[единому контракту](../workflows/checkpoint-recovery.md#контрольная-точка-и-восстановление).
Gmail требуется только при облачной разработке либо явном локальном поручении.
Матрица хранилищ обновляется с фактической
семантикой после внедрения R03.

[Матрица хранилищ](storage-lifecycle.md) · [Анализ чтения](read-snapshot-analysis.md)
