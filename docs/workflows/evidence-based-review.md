# Проверка по доказательствам

Обновлено: **2026-09-13**. Этот порядок применяется к executor/reviewer-проверкам AI poise.
Он дополняет [роли и непрерывность поручения](../governance/development-rules.md#роли-этапов-и-непрерывность-поручения),
но не создаёт новый вид отчёта, reader или gate.

## Что считать находкой

Проверяющий начинает с точного Task contract, текущего результата, изменённых файлов и
обязательств этапа. Для каждого предполагаемого пропуска нужно назвать:

1. подтверждённый объект или обязательство и источник факта;
2. stage, который должен был его создать или проверить;
3. более поздний gate, где отсутствие стало наблюдаемым или блокирующим;
4. конкретную воспроизводимую проверку и ожидаемый результат;
5. владельца причины: AI poise runtime/task contract, исполнитель, проверяющий либо `unresolved`.

Классификация следует доказательствам:

- `runtime/task-contract defect` — принятый контракт требует результат, но route, scope или
  public API не позволяют законно создать, исправить либо передать его;
- `executor omission` — producer был достижим и уполномочен, требование было известно, но
  проверяемого результата нет;
- `reviewer omission` — отсутствие было доступно в предъявленном subject/evidence, однако
  независимый осмотр завершился `clear` без проверки этого обязательства;
- `ordinary gate rejection` — gate корректно остановил неполный или неверный вход; сам
  non-zero/reject не доказывает дефект AI poise runtime;
- `unresolved` — наблюдение подтверждено, но данных недостаточно, чтобы назначить причину.

Нельзя превращать гипотезу в finding, все старые проявления считать текущими дефектами или
перекладывать на агента работу, которую делал недостижимой сам контракт. Исправленный дефект
остаётся regression provenance, но не открытой находкой без нового post-fix наблюдения.

## Чеклист из подтверждённых случаев

| Источник и состояние | Класс подтверждённого случая | Producer stage / объект | Поздний gate | Проверка при ревью |
|---|---|---|---|---|
| Task 0058, HF-08; Task 0063, `RD-013-rework-before-resolution-inspection-v1.md` | runtime lifecycle defect; возможный reviewer omission оценивается отдельно | inspection/remediation: finding, resolution и независимое решение | revise/следующий verify отклоняет состояние с потерянным либо pending resolution | Сопоставить каждый resolution с исходным finding и отдельным inspection decision; запретить rework, пока список pending не пуст; проверить, что отказ не меняет stage/iteration/ownership. |
| Task 0058, HF-09/HF-10; Task 0063, `RD-012-stale-research-observation-contract-v1.md` | runtime contract defect; старый `clear` не переносится на новый subject | verification planning / текущие method definition, schedule и output predicates | test inspection либо evidence gate не может честно принять изменённый тест/источник | Читать текущий verification registry; сверить method identity, stages, covers, executable obligations и predicates с текущим subject. Старый receipt не считать доказательством новой версии. |
| Task 0058, HF-11; Task 0063, `RD-003-newborn-task-definition-v2.md` | подтверждённый task-contract/runtime defect для 0037; остальные scope rejects требуют отдельной классификации | task preparation / producer-consumer order и stage `allowed_paths` | implementation либо artifact/content gate требует недоступный путь | Для каждого обязательного результата пройти все применимые route: producer предшествует consumer, producer writable scope покрывает путь, read-only этап не должен создавать файл. Корректный запрет чужого пути не называть дефектом. |
| Task 0058, HF-12/HF-13/HF-27 | runtime/integration safety defect либо unresolved ownership, не executor omission по умолчанию | integration preparation / accepted commit, target head, conflict resolutions | publication при dirty main, target drift или staged rollback | Проверить неизменяемый accepted commit, обновление только в task worktree, повтор checks после drift, `ff-only` под target lock и точный before/after fingerprint чужого WIP; запретить stash/reset/force. |
| Task 0058, HF-23 и раздел `Structured journal observations not automatically classified as defects` | обычно caller/agent input omission; runtime defect только при доказанной ошибке generated template/docs | agent transport / exact packet fields, query IDs и section names | parser либо query gate отклоняет пакет до domain work | Сопоставить отказ с публичной grammar и выданным template. Если вход нарушил точную форму — исправить пакет; если неверную форму выдал owner API или документация — оформить отдельный runtime finding с источником. |
| Task 0063, `RD-009-legacy-process-migration-deadlock-v1.md` | schema/source compatibility defect, исторически repaired | migration producer / полный process snapshot и audit history | bootstrap новой source требует поля, которых нет в сохранённой Task | Сверить exact route coverage, schema revision, history и source binding до перехода. Старую Task не переписывать reader fallback-ом; новый post-fix сбой требует отдельного evidence. |

Это не универсальный список возможных багов. Строка применяется только когда текущий subject
имеет соответствующее обязательство и проверяемый источник. Finding сообщает конкретный разрыв,
а не название HF/RD само по себе.

## Пакетное чтение предмета

Если независимые чтения известны заранее, отправить их одним публичным `show.queries`. Каждый
query сохраняет собственный уникальный `id`; ответы сопоставляются по нему. Например:

```json
{"operation":"show","input":{"queries":[{"id":"task","kind":"task"},{"id":"report","kind":"section","name":"report","stage":null,"submission":null,"range":null},{"id":"evidence","kind":"evidence"},{"id":"registry","kind":"verification_registry"}]},"messages":[]}
```

Зависимое чтение не угадывают заранее: сначала получают идентификатор/диапазон, затем делают
следующий пакет. Один `show` не обещает единый SQL snapshot разных read-models. Ограничения
`batch.max_items`, диапазонов и output budget остаются действующими. Полный контракт находится
в [пакетном чтении](batch-work.md#пакетное-чтение). Отдельный reader, прямое чтение Task DB и
заявление о не измеренной экономии токенов не нужны.

## Recovery и границы ролей

Чеклист не даёт права обходить gates. Для broken definition используется существующий
`task.restart`; для продвижения к явному этапу — `advance`; для смены executor/reviewer —
сохранение результата, публичный `handoff`, прямое сообщение и новый `bootstrap`. См.
[продвижение к этапу](batch-work.md#продвижение-к-этапу) и
[прямую передачу](local-handoff.md#прямая-передача-между-исполнителем-и-проверяющим).
Самопроверка исполнителя не становится независимым review, а `advance` не заменяет
пользовательскую приёмку перед `publish`.

## Происхождение и границы утверждений

Основной корпус: артефакты HF Task 0058 и
`projects/ai-poise/task/0063/artifacts/root-defects/`. Состояние передачи кандидатов подтверждено
в migration-source Task, в артефакте `future-task-candidates-v9.csv`:
активными остались только N02/N03/N06/N09; документ
`candidate-responsibility-transfer-to-repair-sprint-v1.md` связывает N04/N07 с Task 0074.
Исторические managed artifacts не являются второй канонической политикой и не редактируются
этой задачей.

Корпус не измеряет экономию токенов от batching и не доказывает, что каждый scope/parser reject
является дефектом продукта. Эти ограничения обязательны в отчёте о проверке.
