# Осмотр тестов DDD-04B

Обновлено: **2026-09-06T21:22:42+05:00**. Осмотр тем же агентом, не независимый reviewer.

## До реализации
Контрактные тесты commit `523d255` предшествуют реализации API: ArtifactPlan/Factory, WorkTools, InteractionLedger ещё отсутствовали. Начальный RED в `evidence/ddd-step04b/red.txt` — отсутствие API, не воспроизведённый дефект приложения. Второй test commit `a5a6893` исправил fixtures по действующим min/max поля артефактов и ожиданию сырого текста секции.

## Что охватывается
Пакет генерации всех трёх scopes; явный template/version/parameters; preflight всего списка; symlink/escape/overwrite; одинаковый повтор и другой payload; content gate и точные проверки в том же вызове; новая code tree с прежним результатом; восстановление section layers; адресные ranges; source-scoped event identity/atomic conflict; отмена/restart/rework; CONTINUE без rerun; валидный CLI JSON и real Git demo.

Все прежние test IDs сохранены. Test-only клиент fixture переведён с оперативного файла на client-owned in-memory packet; это не product fallback. Новые public tests используют настоящий WorkTools/Runtime и stdin CLI. Старые fixture task JSON могут читаться драйвером при подготовке исходных данных — это не инструкция агенту редактировать рабочие данные.

Числа лимитов/размеров присутствуют в явных fixtures и конфигурации. Число событий не выводится из количества вызовов. У неизвестного источника нет фиктивного нулевого total. Delivery тестируется как tool result returned, не пользовательская доставка.

Не заявляется test suite для реальных ChatGPT hooks/MCP/Gmail, независимой оценки логического аргумента или всех 13 нормативных процессов. Эти области вне среза.
