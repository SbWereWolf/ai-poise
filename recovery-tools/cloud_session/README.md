# Облачная рабочая сессия: восстановление и поставка

Этот профиль восстанавливает базу PS-DEV-002 от `43e9bc2` и **одну накопительную**
копию. Он не заменяет Task lifecycle и не предполагает, что записанные в старых
отчётах состояние задачи, caller или блокер остаются текущими.

## Входы и новая сессия

Нужны Python 3.13 с venv/pip, Git, Node.js и предоставленный пользователем
`workspace-recover 1.0.0`. SHA-256 дистрибутива:
`8a41a21fb38d83836a45ce6c6d54b7a884fa47e964ec93a2201059672278c15a`.
Не скачивайте другой выпуск вместо проверенного. Три части исходного архива либо
проверенный полный архив имеют совокупный SHA-256:
`d7890704cd40409cebeac0407d7ada39e0da5ce10ce8655e794731635be5be02`.
Накопительная копия и её SHA-256 поступают из последнего подтверждённого handoff.
Она содержит main и один сохранённый Task worktree; отсутствие артефакта до design
обозначается явно, а не заменяется документом другой задачи.

```sh
python recovery-tools/cloud_session/initialize_run.py \
  --run /absolute/NEW/run \
  --destination /absolute/NEW/workdata \
  --parts /absolute/base-parts \
  --checkpoint /absolute/latest-checkpoint.tar.gz \
  --checksum EXACT_CHECKPOINT_SHA256 \
  --tool /absolute/workspace-recover/package \
  --python /absolute/python3.13 \
  --drive-folder VERIFIED_GOOGLE_DRIVE_FOLDER_ID \
  --recipient VERIFIED_HANDOFF_MAILBOX
```

Команда создаёт RUN.json, копию средств и flow; сама ничего не распаковывает и не
захватывает Task. Выполните возвращённый `execute` через workspace-recover. Новый
caller binding — только уникальный путь: Session создаёт штатный identity owner.
Не берите значение POISE_CALLER_BINDING из старого RUN.json. До возобновления
проверьте public ownership; after-crash recovery разрешена только для действительно
остановленной, изолированной прежней сессии. Скрипты не фабрикуют SessionEnd.

Восстановление проверяет части/архив/inventory, восстанавливает Git refs, index/WIP,
Task artifact и operator delta, чинит указатели worktree, проверяет шесть SQLite
и устанавливает сохранённые стабильные wheel-пакеты офлайн **в конечном пути**.
Непроверенный код архива не запускается. В vendor закреплены два совместимых
восстановителя исходного формата, проверяемые VENDOR-SHA256.json до импорта.

Для реальных managed-операций исходного профиля нужны канонические пути
`/home/sbwerewolf/workdata`. Другой корень — проверка файлов/рантайма, не доказанное
перемещение managed config. Генератор не переписывает его и не запускает Task.
Существующие каталоги и повреждённые/неполные входы не перезаписываются.

## Выполнение и копии

`work_flow.py --run RUN --name UNIQUE --packet PACKET.json` выполняет только явный
публичный пакет через стабильный оператор. Source commit и Task acceptance — разные
результаты. Git identity для штатного subprocess берётся из действующего manifest,
не из выдуманного автора или глобального gitconfig. Уникальные имена сохраняют
прошлые неудачные ответы рядом с исправленными, не перезаписывая доказательства.

`snapshot_flow.py --run RUN --name SNAPSHOT --task TASK --note STATE_AND_NEXT`
создаёт накопительную копию; `--artifact apps/.../contract.md` задаёт актуальный
артефакт. До его появления флаг опускается. Публичный backup owner делает Task DB
snapshot; исходная БД не редактируется. В копии сохраняются все изменения operator
от исходной базы, Git delta с достижимыми коммитами, раздельные staged/unstaged WIP,
untracked regular files, manifest и evidence. Не снимайте копию одновременно с
пишущим subprocess. Внебазовые независимые worktree должны иметь отдельную
объявленную поставку; профиль не выдаёт их за автоматически включённые.

Каждый законченный шаг и коммит становится pending milestone. Новые snapshots
создаются через 600–900 секунд, измеренных от подготовки snapshot, не от доставки.
Между ними накапливаются отдельные RED/code/GREEN/commit evidence. Сбой доставки
повторяет **тот же** архив и не сбрасывает часы. Не заканчивайте ответ с работой,
которая не попала в подтверждённую копию.

## Доставка без локальных OAuth-токенов

`delivery_flow.py` принимает `--receipt`, `--run-config`, `--bridge`, `--output`
и `--label`. Flow содержит upload, настоящее скачивание и сверку SHA/size,
формирование письма, отправку и обратное чтение. Изменения sharing не выполняются.
Мост создаётся предоставленным `workspace-recover bridge init` из реально
проверенных capabilities и известного аккаунта. Не объявляйте недоступные права.
`flow host-claim` выдаёт точные connector calls, которые выполняет авторизованный
host. `reply_delivery.py --run RUN --sequence NAME --step STEP --result ACTUAL.json`
передаёт действительный ответ, сохраняет его и продолжает тот же flow. Для download
нужен `--download EXACT_MOUNTED_PATH`; для read-mail — реально прочитанные данные,
а не повтор локального исходящего тела. Перед финальным handoff сопоставьте subject,
recipients и body с исходящим письмом. После неизвестного исхода отправки сначала
прочтите remote state; слепая повторная отправка недопустима.

Недоступный обязательный канал сохраняется как ошибка; работающий канал остаётся,
недостающие данные уходят в ChatGPT Library. Передача final attachment — дополнительная
копия, не замена проверенной внешней записи. Старые signed URLs и archived caller
не становятся новыми полномочиями. Никаких команд push в flow нет.

## Узкая проверка

```sh
(cd recovery-tools/cloud_session/recovery && \
  python -m unittest -v test_restore_continuation test_task_artifact)
(cd recovery-tools/cloud_session && python -m unittest -v test_initialize_run test_snapshot_selection)
PYTHONPATH=src bash tests/smoke.sh /absolute/python3.13
```

Это 18 проверок recovery, 7 проверок инициализации, 4 проверки выбора последнего snapshot и smoke-набор,
не full discovery. Фактический restore-drill и transport receipts сохраняются вне
исходников в очередной копии. Локальная самопроверка не является независимой приёмкой.
