# Ограниченное завершение runner и чтения вывода

Решение `REVIEW-ARCH-A04`, 18 сентября 2026. Подготовлено для R02; это дизайн,
не утверждение о выполненной реализации. База `43bd97b1a6073447aca61eb0b824a821c85f3c7b`.

## Дефект и выбранная граница

[RegisteredCheckRunner](../../src/poise/execution.py) резервирует run_id, создаёт
POSIX process group, два pump-потока и файлы логов. При завершении родителя или timeout
он убивает принадлежащую группу, затем без ограничения делает join потоков. Процесс,
создавший другую сессию, может продолжить держать унаследованный pipe: EOF не наступает.
[Исходный опыт](../../projects/ai-poise/standalone/REVIEW-PLAN-20260918/artifacts/source-review/evidence/R02-unbounded-drain.json)
содержит harmless child на 2 s, hard limit 0.5 s и возврат только после его завершения.

Выбран **однопоточный неблокирующий capture через POSIX selector**, а не отдельные
capture threads. Runner уже требует Linux/WSL и POSIX group ownership; Windows fallback
или новый transport не вводится. Единственный run-thread читает nonblocking pipe FD и
пишет в два принадлежащих ему файла. Lock остаётся только для реестра active/cancel;
другой поток может запросить cancel, но не закрывает pipe/лог.

## Модель состояний и бюджет

```text
RESERVED -> SPAWNED/CAPTURING -> TERMINATING -> DRAINING -> CLOSED
                                  ^ timeout / cancel / child exit / error
```

Все числовые пределы должны быть положительными **конечными**, bool запрещён;
NaN/Infinity не являются способом убрать deadline. `timeout=None` сохраняет явно
неограниченный рабочий интервал, но не снимает конечный teardown после остановки.

У run есть рабочий hard deadline `start + timeout`, progress deadline от последнего
реально прочитанного вывода и отдельный `cleanup_seconds`. Предлагается keyword
`cleanup_seconds=0.25` на уровне RegisteredCheckRunner.run: это документированный
технический бюджет дренирования/ожидания SIGKILL, не бизнес-правило Task и не увеличение
check timeout. Он записывается в результат. Дополнительного обязательного поля во всех
project configs на этом срезе не требуется; caller может передать другой конечный
бюджет. Значение 0.25 s ограничивает остаточное ожидание; полнота всегда отдельный факт,
а не обещание, что любой объём вывода успеет сохраниться за данный интервал.

Selector wait ограничен минимумом poll, остатка рабочего deadline и progress deadline.
На событие читать ограниченный chunk, затем вновь проверять время/cancel: непрерывный
producer не должен вытеснить проверку deadline. После причины остановки назначить
**единственный** cleanup deadline; не продлевать его на каждый pipe/chunk/wait.
При normal child exit не ждать чужой отделившийся процесс; убить только собственную
ещё существующую группу и дренировать до EOF или cleanup deadline.

Существующее SIGKILL группы сохраняется как принудительная ступень завершения: задача
не вводит обязательную grace-церемонию SIGTERM. `child.wait` получает остаток того же
cleanup budget; нулевой остаток не превращается в безграничное ожидание. Если ОС не
подтвердила завершение direct child, вернуть контролируемую ошибку cleanup с run_id,
а не успешно завершённую проверку. Не создавать daemon-читателей в качестве fallback.

В обычных исполнимых POSIX условиях собственные ожидания runner ограничиваются
`hard timeout + cleanup_seconds + dispatch/обычная планировочная погрешность`.
Python-код не гарантирует время возврата при зависшем kernel/filesystem, остановленном
планировщике или произвольно блокирующем стороннем observer; это не надо скрывать под
универсальным real-time обещанием. Разбор строки результата происходит после закрытия
pipes/логов и не должен обращаться к stream, используемому другим потоком.

## Результат и владение

`capture_complete=true` означает EOF обоих потоков. Истечение drain budget при
оставшемся pipe даёт `capture_complete=false`, `capture_reason=drain_limit`.
Частичные stdout/stderr сохраняются как реальные доступные байты. `timed_out` и
`timeout_reason=hard_limit/progress_gap` описывают **команду**, не полноту capture.
Нормально завершившийся parent может иметь exit=0 и incomplete capture — это не обычный
успешный check. `method_passed`, runtime interpretable и package-cache success должны
отклонять явно incomplete capture. Legacy receipts без поля сохраняют старую семантику;
новые runner-результаты всегда содержат поле. Cancel остаётся отдельным исходом.

Все открытые selector/pipe/log FD закрываются тем же run-thread в finally; active-slot
удаляется и после ошибки. Ни один pump не продолжает писать в закрытый target.
`cancel(run_id)` не ищет процессы по имени и не сигналит другой сессии. Detached child
не объявляется принадлежащим runner только из-за наследования stdout. Его pipe-end
закрывается локально; сигнал в чужую process group не посылается.

## Альтернативы

`join(timeout)` без изменения pump отвергнут: поток остаётся живым и гоняется с close.
Закрытие buffered stream из чужого потока само может ждать внутренний lock read.
Stoppable threads с nonblocking FD возможны, но добавляют event/join/resource protocol
без преимуществ для двух POSIX pipes. Selector проще выразить в одном владельце.
Обход закрытием stdout дочерней команды меняет зарегистрированную invocation и потеряет
доказательства. Убийство всего дерева по имени нарушает ownership.

## Проверяемые сценарии внедрения R02

| Сценарий | Обязательное наблюдение |
|---|---|
| Обычный вывод, большой вывод и empty stdout/stderr | Точные байты обоих файлов, EOF, capture_complete=true, прежний exit. |
| Hard/progress timeout | Return bounded с правильной причиной; active пуст, direct child reaped. |
| Detached bounded child держит stdout/stderr | Прежний probe возвращается раньше 1.3 s; partial помечен; child в другой группе не убит. |
| Parent exit=0 с retained pipe | Не timed_out, но incomplete; метод и кэш не считают его обычным passed. |
| Cancel из другого потока / native session end | Только собственный run прекращён; посторонний child остаётся жив. |
| Ошибка selector setup/read/write или observer | Закрыты собственные FD, slot освобождён; следующий запуск работает. |
| Непрерывный поток данных | Проверка hard deadline не голодает. |
| Неконечные/недопустимые бюджеты | Отказ до создания файлов и subprocess. |

Старый тест ошибки запуска pump-thread при отсутствии pump заменяется fault injection
на setup selector/capture с **тем же** смыслом cleanup после частичного старта, не
удаляется как неудобный. Проверки ownership/cancellation остаются независимыми.

## Ввод и откат

Сначала RED probe и новые инварианты, затем capture loop и классификация result,
соседние runner/native cancellation/timeout tests и smoke. Не менять форму argv/cwd,
проверку push или source provenance. Ввод не требует schema migration и не переписывает
исторические receipts. Откат к старому runner возвращает известную liveness-ошибку;
для отката сначала остановить/дождаться active run и сохранить полную копию, не менять
реализацию в середине capture.

[Общий план](review-followup-2026-09-18.md)
