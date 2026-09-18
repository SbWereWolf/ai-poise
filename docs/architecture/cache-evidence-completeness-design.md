# Полнота первичных доказательств при cache reuse

Решение `REVIEW-ARCH-A06`, 18 сентября 2026; реализовано в `REVIEW-BUG-R04`. База `d725d3797325aa603d19c1e696fa3e1987572c1e`.

## Реализация

Cache schema 2 реализует выбранный manifest и явный отказ без autorerun.
[Регрессионные тесты](../../tests/verification/test_cache_evidence_completeness.py)
проверяют доступность, байты, происхождение, перенос checkout и отсутствие побочных
запусков. Исходное поведение ниже — историческая постановка, не текущая гарантия.

## Исходное поведение и терминология

[AiPoiseTestPackageCache](../../src/poise/infrastructure/test_package_cache.py) проверяет
origin result.json по digest cache record, затем возвращает его passed/reused=false→true
без проверки файлов stdout/stderr/JUnit. В [исходном опыте](../../projects/ai-poise/standalone/REVIEW-PLAN-20260918/artifacts/source-review/evidence/R04-cache-missing-junit.json)
успешный origin остаётся реальным, но обычный hit содержит отсутствующий JUnit.
Три понятия различаются: исторический тест passed; первичные доказательства доступны
и целостны сейчас; Task приняла эти доказательства. Кэш отвечает только за первые два.

## Выбранное решение

**Обычный reuse разрешён только для полного проверенного primary-evidence bundle.**
При существующем, но неполном/повреждённом record возвращать контролируемую PoiseError
с точным именем проблемного файла и указанием `--fresh`. Никакого автоматического
повторного pytest при потере evidence. При отсутствии самого cache record — обычный
cache miss и новый явно запрошенный run, как прежде. Отличие повреждённого record от
его отсутствия сохраняется. Поле passed старого origin не переписывается в false;
ошибка означает, что текущий reuse не доказан.

| Артефакт | Обязательность / проверка |
|---|---|
| Cache record | schema, package_id, content fingerprint, безопасный origin_run_id, путь origin result и digest. |
| Origin result.json | Обычный локальный файл, точный SHA-256 из record, совпадение run/package/fingerprint, passed=true, capture не помечен incomplete. |
| stdout.log | Всегда обязателен, даже нулевой длины; относительный путь, byte size и SHA-256. |
| stderr.log | Всегда обязателен, даже нулевой длины; относительный путь, byte size и SHA-256. |
| junit.xml | Обязателен для passed; terminal nonempty testcase report, byte size и SHA-256. |
| command/cases/exit/duration | Сохраняются в result.json и защищены его digest; не являются новой execution при reuse. |
| Task EvidenceRepository receipts | Не источник данного codebase-only кэша, не дублируются в другую БД. |

Новый result содержит versioned `primary_evidence` manifest ровно для stdout/stderr/junit:
`{path, bytes, sha256}` каждого. Пути должны совпадать с соответствующими result-полями
и лежать в собственном cache evidence directory данного run. Manifest добавляется
после завершения capture и закрытия файлов, до result.json/cache-record publication.
Result защищён digest из record. Итоговая cache schema получает новую версию, input
fingerprint остаётся прежним. Это integrity/provenance цепочка, не криптографическая
подпись против субъекта, способного переписать и record, и все его файлы.

Проверка пути выполняется **до resolve**, чтобы не потерять факт symlink. Отвергать
абсолютные пути, traversal, symlink-компоненты/alias на другой run или выход за checkout,
необычные типы файлов. Во время открытия/хеширования исчезновение файла также даёт
контролируемую неполноту, не успешный hit и не необработанный FileNotFoundError.
SHA сверяется над полными байтами, не preview. Пустые обязательные stdout/stderr допустимы.

## Состояния и совместимость

| Состояние | Результат |
|---|---|
| Record отсутствует | Fresh execution обычного запроса, новый run_id. |
| Record и весь bundle новой версии корректны | reused=true, executed=false, исходный origin_run_id, completeness подтверждена. |
| Исчез/изменён stdout, stderr, JUnit, origin result | Явная ошибка incomplete/changed evidence, без subprocess и без удаления старого record. |
| Record старой версии без manifest | Явная ошибка legacy evidence; `--fresh` создаёт новый полный origin. Не сочинять digest утраченных логов. |
| Неуспешный/прерванный/explicit incomplete capture fresh run | Сохранить диагностический result, не создавать reusable record. |
| Пользователь передал --fresh | Явный новый запуск, успешный результат становится новым origin; старый каталог не удалять. |

C018 сохраняется: [fingerprint](../configuration/test-package-cache.md) состоит **только**
из membership и содержимого source/tests/fixtures. Python/env/timeout/absolute
checkout path не добавляются в ключ. Полный cache hit не доказывает работоспособность
в новой среде; для такой проверки нужен --fresh. Перенос всего checkout вместе с cache
остаётся допустимым благодаря относительным путям и неизменным байтам.

## Альтернативы, retention и риски

Автоматически считать неполный record miss проще, но скрывает утрату доказательств и
без отдельного намерения повторяет внешние эффекты pytest. Summary-only режим возможен
как будущая отдельная read-команда с evidence_complete=false и отсутствующими links,
но смешивать его с обычным passed hit нельзя. На данном срезе режим не добавляется.
Проверять только existence недостаточно: другой файл того же имени не тот же receipt.
Включение env в key не устраняет пропавшие primary files и нарушает текущий C018.

Retention целого origin bundle — обязанность codebase cache владельца. Не удалять
primary files живого record; освобождение места должно удалять весь obsolete record
и соответствующий bundle, не переписывать историю успешного теста. R04 не вводит
автоматическую очистку и не считает backup Task DB копией файлов кэша. Проверка гарантирует
наблюдаемую целостность в момент hit, не существование файла после возврата. Несогласованная
ручная очистка будет обнаружена следующим чтением. Криптографическая authenticity и
защита от привилегированного concurrent attacker не заявляются.

## Проверки, ввод и откат R04

RED/GREEN: исходные три missing probes. Для каждого stdout/stderr/JUnit/result — missing,
замена байтов с тем же размером, symlink и malformed manifest; каждый случай отказывает
без вызова runner. Дополнительно legacy record без manifest, неверный origin/fingerprint,
валидный hit с runner запретом, explicit fresh после ошибки, relocation всего checkout,
неуспешный/timeout/partial-capture run без reusable record. Свежий origin должен иметь
manifest реальных файлов, а не ожидаемые test constants, скопированные из результата.

Ввод не требует изменения Task/Requirements schema или пользовательской БД. Не трогать
content fingerprint. Старые cache records оставлять как историю, явно предложить fresh.
Откат программы на старую cache schema требует отдельного свежего запуска, не удаления
primary доказательств или принудительного объявления новой schema старой. Все материалы
R04 и чекпоинты сохраняются отдельно от временных pytest caches.

[Общий план](review-followup-2026-09-18.md) · [Runner capture](runner-capture-shutdown-design.md)

## Последующее уточнение ключа и владения

Хэши полных байтов исходников, тестов и фикстур остаются требованием. Четвёртая
обязательная группа и новая JSON-схема не требуются. После обзора `61151e5` пользователь
уточнил модель: одно рабочее дерево принадлежит одной сессии, кэш локален этому дереву;
прежнее требование отдельного межпроцессного владения cache resource снято.
Канонический контракт: [хэши входов](../configuration/test-package-cache.md#хеши-байтов-по-группам-входов)
и [владение локальным кэшем](../configuration/test-package-cache.md#монопольная-запись-через-владение).

Сценарий намеренной подмены исходников во время теста остаётся вне задачи;
[обычный режим выполнения](../configuration/test-package-cache.md#обычный-режим-выполнения-тестов)
не требует защиты от злонамеренного агента. Завершение R04 означает полноту evidence,
не атомарную запись индекса. Неатомарность сохранена в
[реестре известных багов](../operations/known-bugs.md#rv2-02--прерванная-запись-индекса-кэша)
без поручения добавлять cache-lock или операторскую обвязку.
