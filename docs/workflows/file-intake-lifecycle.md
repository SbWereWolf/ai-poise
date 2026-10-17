# Жизненный цикл общего чтения файлов

## Допуск и владение

Общий reader работает без Task/Sprint, но со явно выбранными project/checkout/policy и настоящей
caller identity. Общий SessionEstablisher сохраняет native/generated происхождение; допуск ресурса
дополнительно требует подтверждённый active episode настроенного HookService. Native session ID
не равен автоматически internal binding ID: используется существующий latest binding и его
диагностика. Caller не передаёт собственное доказательство End в read/recover packet.

Настройки и установка native owner принадлежат [runtime hooks](../configuration/runtime-hooks.md).
Рабочий session launcher не создаётся reader и не переключается на файлы прочитанного checkout.
Все параметры остаются у [владельца политики](../configuration/project-file-reading.md#справочник-опций).
`adopt` подтверждает уже имеющуюся собственную active запись/дерево; он не создаёт владение при
его отсутствии и не присваивает чужой ресурс.

## Размещение ресурсов

Внутри `session.temporary_root` создаётся каталог caller/episode. В нём находятся owner record,
`registry_directory/registry.filename` и `response_directory` с полными ответами чтения.
Отдельный `coordination_root` хранит caller gate/authoritative owner record. `diagnostic_file`,
`registry_template` и `storage.control_response_root` также внешние: recovery их не удаляет.
Их вложение внутрь temporary root отклоняется как `lock_resources_conflict`.

Политика и локальные значения не коммитятся в исходный код; используйте
[полные примеры и заполнение](../configuration/project-file-reading.md#подготовка-политики-и-примеры).
Не меняйте корни под живым владельцем, чтобы обойти диагностику. Сохраняйте исходные bytes и
разбирайте authoritative episode через его установленного владельца.

## Подготовка шаблона

1. Заполните рабочую политику и выберите новый свободный `session.registry_template` вне temporary root.
2. Отправьте `{"operation":"prepare_template"}` через общий CLI с явными абсолютными входами.
3. При успехе проверьте `details.path`, `schema_version=2`, `sha256` и `status="prepared"`.

Шаблон — закрытая пустая schema-2 SQLite, построенная из versioned Python resource установленного
пакета. Live строки не импортируются, schema не мигрируется на горячем пути чтения.
При prepare новый реестр копируется только из проверенного пустого шаблона; существующий реестр
не подменяется копией. Структура, marker и constraints проверяются до использования.

| Исход | Наблюдаемый результат | Действие |
| --- | --- | --- |
| До публикации не удалось подготовить файл | `template_preparation_failed`, failed/exit 1, details null, target отсутствует | Устранить причину, повторить по свободному адресу. |
| Публикация успешна | ready/exit 0, details status prepared и SHA | Использовать подготовленный шаблон. |
| После публикации долговечность не подтверждена | `template_publication_unconfirmed`, unconfirmed/exit 1, details status published_durability_unconfirmed и SHA; валидный target сохранён | Сохранить bytes и подготовить новый шаблон по новому свободному адресу. |
| Адрес уже занят | `template_exists`, rejected/exit 2 | Сохранить существующий файл, выбрать свободный адрес. |

Повтор по занятому адресу не заменяет шаблон. Не удаляйте его, чтобы скрыть неподтверждённую
публикацию. Ошибки и точные operator actions перечислены в
[справочнике](../configuration/project-file-reading.md#ошибки-и-коды-завершения).

## Подготовка и активное чтение

После настоящего Start своего native episode вызовите `prepare`, затем `read`/`context`.
Expensive native/template observation выполняется перед коротким gate; допуск owner, live schema
validation, generation/budget snapshot и registry writes согласуются существующим gate.
Чтение source-файла готовит выбранный текст; cache/events фиксируются отдельно на каждый item.
Сохранение полного read result проверяет актуальность владельца до публикации stdout.

Owner identity включает project, caller key, episode, resource path, registry/response names и
настроенные hook paths. Неверная/нечитаемая запись не переписывается автоматически. JSON null,
array, bool, number или string в существующем owner file даёт structured `ownership_unknown` с
сохранностью файла и дерева. Только фактическое отсутствие файла означает отсутствие записи.
Корректный прежний ended owner допускает уже очищенный отсутствующий каталог; это позволяет
следующему подтверждённому episode законно подготовить новый ресурс.

`advance_context` — только явный `reason="manual"`. Повтор старого request_id возвращает generation
сохранённого перехода, но не уменьшает живое generation; последующий `context` наблюдает текущий.
Это не автоматическая обработка compaction и не acknowledgement.

## Завершение и восстановление

После настоящего exact SessionEnd своего episode отправьте `{"operation":"recover"}`.
Recovery выбирает конкретные episode/path, затем перечитывает authoritative owner под gate.
Наблюдение End идёт у существующего native owner в пределах обоих настроенных бюджетов;
непосредственно перед удалением повторно проверяется точный receipt End этого episode.

| Состояние | Наблюдаемый исход |
| --- | --- |
| Подтверждённый End, свой существующий каталог | Запись ending, удаление только своего whole temporary tree, запись ended, cleaned/exit 0. |
| Подтверждённый End, свой каталог уже отсутствует | Запись ended, already_absent/exit 0; повтор безопасен. |
| End не доказан, состояние неизвестно/ещё active или бюджет исчерпан | Честный отказ/incomplete, подтверждённая очистка не выдумывается. |
| Ошибка удаления, сохранения или диагностики | cleanup_incomplete/incomplete/exit 1; сохраняются фактические частичные эффекты. |
| Пока R1 ждал gate, R2 очистил старый A и допущен новый B | stale/episode_mismatch/exit 1, details выбранного A; owner/tree/registry B сохраняются. |
| Текущий authoritative owner невозможно подтвердить | structured ownership_unknown, без переименования или сброса чужих данных. |

При том же episode recovery использует свежую фазу после gate, а не прежнюю копию record.
В stale случае дальнейшие context/read B остаются допустимыми. Stale/ownership отказы не
перекрашиваются в успешный already_absent или общий incomplete.

При incomplete прочитайте полный сохранённый ответ и внешний diagnostic log. Ошибка может
возникнуть после фактического удаления дерева: это не основание возвращать уже удалённые bytes.
Когда причина устранена и тот же End подтверждён, повтор может законно дать already_absent.
Coordination record/gate, внешний diagnostic log, template и control responses сохраняются.
Нельзя удалять lock/переписывать owner или фабриковать End как обычную процедуру восстановления.

## Границы поставки и проверка

Общий read API, schema-2 template, реестр, native episode admission и recovery доступны через
установленный пакет; поддерживается [полный JSON и text framing](../configuration/project-file-reading.md#полный-json-и-text-framing).
Произвольные обычные инструменты не перехватываются этим API; общего автоматического ledger
всех tool results и автоматического compaction reset здесь нет.
[Специализированный SourceReader](source-reader.md#явные-диапазоны-и-поколения-контекста)
с его acknowledgement/reset сохраняет собственный контракт.

Два поставленных профиля проходят реальный offline installed-package consumer с изолированными
SDK Start/End; рабочие hook settings пользователя для этой проверки не меняются. Регрессии
отдельно проверяют malformed/non-object owner и чередование старого recovery с новым episode,
а также нормальное incomplete/cleanup/replay. Проверки ссылок не заменяют эти поведенческие факты.
