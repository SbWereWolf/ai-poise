# Извлечение библиотек из прототипа и проверки архитектуры

Создано: **2026-09-06T16:04:53+05:00**.

Статус: **проект следующей реализации, не выполненный refactoring**.

## 1. Что действительно есть в прототипе

Исходники прочитаны из неизменённого `harness-happy-path_2026-09-06T14-30-44+05-00.zip`. Это не новый полный code review. [Индекс деклараций](../evidence/prototype-symbols.json) фиксирует просмотренные классы и методы с номерами строк.

| Существующее место | Что уже даёт | Целевое место | Что не надо делать |
|---|---|---|---|
| `runtime.py: Harness.bootstrap` | Привязка текущей работы, создание контекста | `BootstrapWork` + sessions/tasks + ContextAssembler | Не писать второй bootstrap для sprint с копией всего тела |
| `runtime.py: Harness._stage` | Этап из конфигурации | processes + общий runner | Не ветвиться по goal_type |
| `runtime.py: Harness.verify` | Кандидатный payload, команды, evidence, finalization | `VerifyStage`, TaskCommands, verification, FinalizationCoordinator | Не переносить весь большой метод в `BaseHandler` |
| `runtime.py: _tree/_changed` | Наблюдение Git без регистрации файлов моделью | SQLite-independent WorkspacePort/Git adapter | Не заставлять агента сообщать изменённые файлы |
| `runtime.py: _select_checks` | Объединение явно определённых методов | verification + execution policy | Не добавлять автоматическое угадывание команд по проекту |
| `runtime.py: _publish` | Сверка дерева, commit/private push, receipt | Workspace publisher + FinalizationCoordinator | Не смешивать private push и integration target publish |
| `runtime.py: accept/cancel` | Простейшие бизнес-переходы | Task aggregate методы + use cases | Не оставлять прямые правки dict статуса в CLI |
| `storage.py: Store.transaction` | Внешний flock и короткий SQL commit | SqliteUnitOfWork | Не брать lock на всю длительность тестов |
| `storage.py: Store.save/current` | Сохранение/чтение task JSON | TaskRepository и Query adapters | Не считать giant JSON задач окончательной DDD-моделью |
| `artifacts.py: inspect_paths/check_counts` | Path-only ввод, roots, count rules | ArtifactRegistry + PathInspector | Не возвращать purpose/type/hash в обязанности агента |
| `execution.py: run_command/preview` | Точный запуск и краткий вывод | Execution adapter + Output parser | Не прятать business acceptance в runner subprocess |
| `__main__.py` | CLI parsing и вызовы текущего Harness | Тонкий CLI adapter над HarnessApplication | Не сохранять бизнес-данные напрямую из presentation layer |

То, что части прототипа смешаны в одном классе, нормально для первого среза. Теперь новая бизнес-логика должна идти в библиотеку-владельца, а общая механика выноситься по мере подключения настоящих маршрутов. Не нужен массовый косметический перенос файлов до работающего следующего примера.

## 2. Как вводить DDD без остановки разработки

| Срез | Реальная работа | Проверка на маршруте |
|---|---|---|
| 1. Доступ к данным и текущая Task | Task aggregate, content API, UoW/Repository, Query projection, typed submission | Существующий короткий development demo без изменения его результата |
| 2. Общий runner | Вынести phase/attempt/finalization; подключить produce и inspect | Development + документационный результат; один цикл rework |
| 3. Доказательства | verification library, exact methods, receipt adapter, manual PREPARE/CONTINUE | Командная и логическая verification без вымышленных полей |
| 4. Sprint library | Draft publication, membership/DAG, eligible query, task completion reactions | Sprint planning публикует валидные дочерние tasks; затем одна выполняется |
| 5. Остальные общие handler bodies | observe/check/revise/apply_plan/publish | Один профильный пример наблюдения, исправление среды, конфликт integration |
| 6. Полный маршрутный набор | Подключить независимые конфиги через уже существующие библиотеки | Все 13 коротких и полных маршрутов из предыдущего комплекта |

Это последовательность архитектурного извлечения, не требование по 13 отдельным реализациям. Приоритет — работающий сквозной маршрут, затем feedback и его обычные ошибки, как уже договорились.

Если нормализованное хранение несовместимо с БД прототипа, новая реализация работает на явно созданном пустом store/тестовых данных. Старый store остаётся нетронутым. Миграция пользовательских задач/спринтов требует отдельного прямого разрешения; в этом комплекте её нет.

## 3. Архитектурные проверки при реализации

Это будущие checks, а не заявление об их выполнении на ещё не созданном коде.

| Проверка | Что ловит | Уровень |
|---|---|---|
| Запрет импортов infrastructure/CLI/SQLite/subprocess из domain | Смешение бизнеса и способа выполнения | Статический архитектурный test |
| Допустимые зависимости библиотек без циклов | Сплетение task/sprint/runtime | Статический test |
| Все adapters вызывают HarnessApplication | Обход единого pipeline | Contract/integration test |
| Только owner repositories изменяют свои таблицы | SQL write мимо агрегата | Архитектурный + integration test |
| Task API не позволяет принять собственный ResolutionProposal как reviewed | Потеря границы осмотра | Domain unit test |
| Кандидатные новые секции/методы участвуют в текущем verify | Проверка старого шаблона вместо результата | Integration test |
| Повтор payload + новое дерево создаёт новый check, но не новый слой | Ложный replay и раздувание истории | Integration test |
| Stage verified не начинает next без инструкции | Нарушение человеческой границы | Route test |
| Task cancellation не отменяет siblings/root без соответствующей санкции | Старый scope expansion | Domain + integration test |
| Path-only registration одинаково работает для task/sprint/ad-hoc runtime | Дублированные и несовместимые tools | Contract test |
| DB lock снят во время тестов A и B в разных worktrees | Скрытая глобальная сериализация работы | Integration test |
| Task/sprint sections читаются по owner/index без полного обхода истории | Ненужная административная нагрузка | Query/план запроса + bounded fixture |
| Успешный test verdict не переписывается renderer | Старый ложный failure | Execution/output contract test |
| Task completion/reopen/cancel правильно меняет credit/reversal | Потеря экономики результата | Domain + metrics test |

Для DDD архитектурные tests дополняют реальные маршруты. Они не доказывают содержательную корректность осмотра моделью. Недостаточно только обнаружить импорт `sqlite3`: wrapper мог скрыть I/O, поэтому важен и review границ ответственности.

## 4. Небольшой набор сквозных проверок границ

**Заполнение секции.** Агент подаёт два текста одним result package. Task создаёт новые слои; старые остаются. Inspector API не может заменить предмет, который осматривает. Следующая команда show читает нужный слой, не всю task.

**Артефакт.** Агент подаёт три пути из разрешённых roots. Owner определяется библиотекой. Повтор одного пути не увеличивает количество. Ссылка на чужой owner отклоняется. Код целевой codebase не выдаётся за внутренний task artifact.

**Наблюдение и продолжение.** Executor возвращает факты; агент подаёт предусмотренный CONTINUE. Application не повторяет выполненный шаг, а Task сохраняет аргумент, связанный с этими фактами.

**Осмотр и исправление.** Finding сохраняется у task, исправление — отдельным proposal, повторный inspect принимает/отклоняет его. Код handler inspect один для кода и документа, но критерии независимы.

**Спринт.** Один PublishSprintPlan создаёт tasks, каждая валидируется собственным goal_type. Неполный draft не публикуется. Завершение одной task актуализирует готовность другой без отдельного вызова LLM и без открытия всей истории.

**Параллельные разные задачи.** Два worktree одного приложения меняют один файл. Короткие DB записи сериализуются, но проверки и coding work независимы. Гонка за одной task и cancel посреди verify остаются вне согласованной модели.

**Приёмка.** `accept-only` и `accept+continue` дают разные действия при одинаковом принятом результате. Для последовательного `executor`-участка действующее поручение исполнителю позволяет повторять `accept+continue` без сообщения «дальше»; `reviewer` и отдельно регулируемая publication требуют новой явной команды.

**Сбой внешнего действия.** После неизвестного результата push возвращается controlled state; следующий вызов проверяет receipt. При отсутствии надёжного probe допускается конкретная остановка без ложного успеха. Не нужен универсальный recovery framework до появления практики.

## 5. Что сознательно не строим

Не создаём микросервис для task/sprint, брокер сообщений, полноценный event sourcing, отдельную read DB, ORM ради ORM, generic repository с доступом ко всем таблицам, дерево наследования goal types, универсальный workflow DSL, систему автоматического содержательного merge задач или реестр смысловых деклараций артефактов.

Весь список 35 логических таблиц — карта ответственности данных для целевого продукта, не prerequisite построить 35-table framework прежде, чем исполнить один маршрут. В каждом срезе реализуются реально используемые записи и их инварианты. Ранее работающие функции сохраняются и проверяются; конфигурационная полнота активного среза обязательна.

## 6. Что изменилось в документации, но не в коде

Принятый новый инвариант: DDD и доменные API обязательны для будущей реализации. Уточнены владельцы task/sprint data, общий код tools, repositories/query access, границы SQLite/filesystem и обработчиков.

Не меняются 13 независимых процессов, семь семейств handler, 98 узлов и 34 именованных feedback-перехода. Не меняются path-only artifacts, один этап на пользовательский рабочий цикл, exact verification commands, Linux/Python, отсутствие defaults/fallbacks/migrations без санкции.

[Карта этапов](../stage-library-map.md) показывает, какие библиотеки вызываются на каждом узле. Это проверка полноты проектного сопоставления, не выполнение продуктовых сценариев.
