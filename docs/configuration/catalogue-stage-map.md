# Карта исполняемого каталога DDD-09

Обновлено: **2026-10-28**.

Семантика и границы: [workflow-catalogue](workflow-catalogue.md).

Все 13 разделов соответствуют принятым корневым определениям: 143 узла.
Роль, назначение, переходы и разрешённые возвраты читаются из самого workflow;
роль не выводится из handler или имени этапа. Пользователь назначает роли агентам;
один агент может исполнять обе назначенные роли. См. [правило ролей](../governance/development-rules.md#роли-этапов-и-непрерывность-поручения).

Справочник именует 52 возврата: все 47 переходов changes_requested к разрешённому
rework target десяти перенесённых маршрутов и пять сохранённых связей development,
test_development и verification. Полный граф определяется исходниками, включая
остальные переходы этих трёх маршрутов. В review переход inspection → execution_inspection
при changes_requested является движением к проверке отчёта, а не rework-ребром;
он сохранён в таблице маршрута.
Историческая карта 102 узлов сохранена отдельно: 83 идентификатора остаются в текущих
маршрутах, 19 выведены из них, 60 текущих идентификаторов добавлены позднее.

## Черновик назначений навыков этапа

Планируемое дополнение шаблона этапа — метанавыки и предметные навыки, которые
постановщик уточняет при создании Task. Это **не реализованные поля** текущего
каталога и не новые роли: карта ниже по-прежнему содержит `executor`/`reviewer`.
См. [два набора навыков](../workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
и [отложенные решения по tooling](../workflows/task-stage-skills-draft.md#что-существует-сейчас-и-что-отложено).

## development

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| baseline | observe | executor | Сбор исходных наблюдений | complete → baseline_inspection | baseline |
| solution_planning | produce | executor | Проектирование ближайшего исполнения | complete → solution_planning_inspection | solution_planning |
| verification_planning | produce | executor | Проектирование ближайшего исполнения | complete → verification_planning_inspection | verification_planning |
| test_implementation | produce | executor | Выполнение согласованного проекта | complete → test_inspection | test_implementation |
| test_inspection | inspect | reviewer | Независимый осмотр результата | changes_requested → test_implementation; clear → implementation_design | test_inspection, test_implementation |
| implementation | produce | executor | Выполнение согласованного проекта | complete → implementation_inspection | implementation |
| implementation_inspection | inspect | reviewer | Независимый осмотр результата | changes_requested → implementation; clear → documentation_planning | implementation_inspection, implementation |
| documentation | produce | executor | Выполнение согласованного проекта | complete → documentation_inspection | documentation |
| baseline_inspection | inspect | reviewer | Независимый осмотр результата | changes_requested → baseline; clear → solution_planning | baseline |
| documentation_inspection | inspect | reviewer | Независимый осмотр результата | changes_requested → documentation; clear → завершение | documentation |
| documentation_planning | produce | executor | Проектирование ближайшего исполнения | complete → documentation_planning_inspection | documentation_planning |
| documentation_planning_inspection | inspect | reviewer | Независимый осмотр результата | changes_requested → documentation_planning; clear → documentation | documentation_planning |
| implementation_design | produce | executor | Проектирование ближайшего исполнения | complete → implementation_design_inspection | implementation_design |
| implementation_design_inspection | inspect | reviewer | Независимый осмотр результата | changes_requested → implementation_design; clear → implementation | implementation_design |
| solution_planning_inspection | inspect | reviewer | Независимый осмотр результата | changes_requested → solution_planning; clear → verification_planning | solution_planning |
| verification_planning_inspection | inspect | reviewer | Независимый осмотр результата | changes_requested → verification_planning; clear → test_implementation | verification_planning |


## test_development

Подробный [маршрут и критерии](../workflows/paired-task-stages.md#исполнимый-маршрут-test_development),
а также [исходник, подготовленная форма и историческое сопоставление](workflow-catalogue.md#самостоятельная-разработка-тестов).

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| baseline | observe | executor | Сбор исходных наблюдений | complete → baseline_inspection | baseline |
| baseline_inspection | inspect | reviewer | Независимый осмотр результата | clear → coverage_planning; changes_requested → baseline | baseline |
| coverage_planning | produce | executor | Проектирование ближайшего исполнения | complete → coverage_planning_inspection | coverage_planning |
| coverage_planning_inspection | inspect | reviewer | Независимый осмотр результата | clear → test_planning; changes_requested → coverage_planning | coverage_planning |
| test_planning | produce | executor | Проектирование ближайшего исполнения | complete → test_planning_inspection | test_planning |
| test_planning_inspection | inspect | reviewer | Независимый осмотр результата | clear → test_implementation; changes_requested → test_planning | test_planning |
| test_implementation | produce | executor | Выполнение согласованного проекта | complete → sensitivity_verification | test_implementation |
| sensitivity_verification | check | executor | Механическое наблюдение чувствительности | satisfied → test_inspection; not_satisfied → test_implementation; inconclusive → test_implementation | sensitivity_verification, test_implementation |
| test_inspection | inspect | reviewer | Независимый осмотр результата | clear → завершение; changes_requested → test_implementation | test_inspection, test_implementation |


## verification

Подробные [этапы и критерии](../workflows/paired-task-stages.md#исполнимый-маршрут-verification),
[метод VERIFY и поставка пары](workflow-catalogue.md#проверка-соответствия-результата).

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| planning | produce | executor | Проектирование ближайшего исполнения | complete → planning_inspection | planning |
| planning_inspection | inspect | reviewer | Независимый осмотр программы и решение по критериям execution | clear → execution; changes_requested → planning | planning |
| execution | check | executor | Выполнение согласованной программы наблюдений | satisfied → analysis; not_satisfied → analysis; inconclusive → analysis | execution |
| analysis | produce | executor | Анализ наблюдений по принятой программе | complete → execution_inspection | analysis, execution |
| execution_inspection | inspect | reviewer | Независимый осмотр результата и доказательств | clear → завершение; changes_requested → execution | execution_inspection, execution |

## review

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| planning | produce | executor | Проектирование ближайшего исполнения | complete → planning_inspection | planning |
| planning_inspection | inspect | reviewer | Проверка результата | clear → inspection; changes_requested → planning | planning |
| inspection | inspect | executor | Получение и оценка фактов | clear → execution_inspection; changes_requested → execution_inspection | inspection |
| execution_inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → inspection | execution_inspection, inspection |


## design

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Проектирование ближайшего исполнения | complete → framing_inspection | framing |
| framing_inspection | inspect | reviewer | Проверка результата | clear → alternatives; changes_requested → framing | framing |
| alternatives | produce | executor | Исполнение согласованного плана | complete → alternatives_inspection | alternatives |
| alternatives_inspection | inspect | reviewer | Проверка результата | clear → decision; changes_requested → alternatives | alternatives |
| decision | produce | executor | Проектирование ближайшего исполнения | complete → decision_inspection | decision |
| decision_inspection | inspect | reviewer | Проверка результата | clear → detailed_design; changes_requested → decision | decision |
| detailed_design | produce | executor | Исполнение согласованного плана | complete → design_verification | detailed_design |
| design_verification | check | executor | Получение и оценка фактов | satisfied → design_inspection; not_satisfied → detailed_design; inconclusive → detailed_design | design_verification, detailed_design |
| design_inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → detailed_design | design_inspection, detailed_design |


## analysis

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Проектирование ближайшего исполнения | complete → framing_inspection | framing |
| framing_inspection | inspect | reviewer | Проверка результата | clear → evidence_collection; changes_requested → framing | framing |
| evidence_collection | observe | executor | Получение и оценка фактов | complete → evidence_collection_inspection | evidence_collection |
| evidence_collection_inspection | inspect | reviewer | Проверка результата | clear → analysis_planning; changes_requested → evidence_collection | evidence_collection |
| analysis_planning | produce | executor | Проектирование ближайшего исполнения | complete → analysis_planning_inspection | analysis_planning |
| analysis_planning_inspection | inspect | reviewer | Проверка результата | clear → reasoning; changes_requested → analysis_planning | analysis_planning |
| reasoning | produce | executor | Исполнение согласованного плана | complete → challenge | reasoning |
| challenge | produce | executor | Исполнение согласованного плана | complete → analysis_inspection | challenge, reasoning |
| analysis_inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → reasoning | analysis_inspection, reasoning |


## profiling

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Проектирование ближайшего исполнения | complete → framing_inspection | framing |
| framing_inspection | inspect | reviewer | Проверка результата | clear → baseline_measurement; changes_requested → framing | framing |
| baseline_measurement | observe | executor | Получение и оценка фактов | complete → baseline_inspection | baseline_measurement |
| baseline_inspection | inspect | reviewer | Проверка результата | clear → experiment_planning; changes_requested → baseline_measurement | baseline_measurement |
| experiment_planning | produce | executor | Проектирование ближайшего исполнения | complete → experiment_planning_inspection | experiment_planning |
| experiment_planning_inspection | inspect | reviewer | Проверка результата | clear → measurement; changes_requested → experiment_planning | experiment_planning |
| measurement | observe | executor | Получение и оценка фактов | complete → measurement_inspection | measurement |
| measurement_inspection | inspect | reviewer | Проверка результата | clear → analysis_planning; changes_requested → measurement | measurement |
| analysis_planning | produce | executor | Проектирование ближайшего исполнения | complete → analysis_planning_inspection | analysis_planning |
| analysis_planning_inspection | inspect | reviewer | Проверка результата | clear → analysis; changes_requested → analysis_planning | analysis_planning |
| analysis | produce | executor | Исполнение согласованного плана | complete → confirmation | analysis |
| confirmation | check | executor | Получение и оценка фактов | satisfied → synthesis; not_satisfied → synthesis; inconclusive → synthesis | confirmation |
| synthesis | produce | executor | Исполнение согласованного плана | complete → profiling_inspection | synthesis |
| profiling_inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → analysis | analysis |


## environment_diagnostics

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Проектирование ближайшего исполнения | complete → framing_inspection | framing |
| framing_inspection | inspect | reviewer | Проверка результата | clear → reproduction; changes_requested → framing | framing |
| reproduction | observe | executor | Получение и оценка фактов | complete → reproduction_inspection | reproduction |
| reproduction_inspection | inspect | reviewer | Проверка результата | clear → hypothesis_planning; changes_requested → reproduction | reproduction |
| hypothesis_planning | produce | executor | Проектирование ближайшего исполнения | complete → hypothesis_planning_inspection | hypothesis_planning |
| hypothesis_planning_inspection | inspect | reviewer | Проверка результата | clear → experiments; changes_requested → hypothesis_planning | hypothesis_planning |
| experiments | observe | executor | Получение и оценка фактов | complete → experiments_inspection | experiments |
| experiments_inspection | inspect | reviewer | Проверка результата | clear → analysis_planning; changes_requested → experiments | experiments |
| analysis_planning | produce | executor | Проектирование ближайшего исполнения | complete → analysis_planning_inspection | analysis_planning |
| analysis_planning_inspection | inspect | reviewer | Проверка результата | clear → root_cause_analysis; changes_requested → analysis_planning | analysis_planning |
| root_cause_analysis | produce | executor | Исполнение согласованного плана | complete → confirmation | root_cause_analysis |
| confirmation | check | executor | Получение и оценка фактов | satisfied → synthesis; not_satisfied → synthesis; inconclusive → synthesis | confirmation |
| synthesis | produce | executor | Исполнение согласованного плана | complete → diagnostics_inspection | synthesis |
| diagnostics_inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → root_cause_analysis | root_cause_analysis |


## environment_remediation

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Проектирование ближайшего исполнения | complete → framing_inspection | framing |
| framing_inspection | inspect | reviewer | Проверка результата | clear → baseline; changes_requested → framing | framing |
| baseline | observe | executor | Получение и оценка фактов | complete → baseline_inspection | baseline |
| baseline_inspection | inspect | reviewer | Проверка результата | clear → change_planning; changes_requested → baseline | baseline |
| change_planning | produce | executor | Проектирование ближайшего исполнения | complete → change_planning_inspection | change_planning |
| change_planning_inspection | inspect | reviewer | Проверка результата | clear → application; changes_requested → change_planning | change_planning |
| application | apply_plan | executor | Исполнение согласованного плана | complete → verification | application |
| verification | check | executor | Получение и оценка фактов | satisfied → inspection; not_satisfied → application; inconclusive → application | verification |
| inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → application | application |


## documentation

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Проектирование ближайшего исполнения | complete → framing_inspection | framing |
| framing_inspection | inspect | reviewer | Проверка результата | clear → outline; changes_requested → framing | framing |
| outline | produce | executor | Исполнение согласованного плана | complete → outline_inspection | outline |
| outline_inspection | inspect | reviewer | Проверка результата | clear → drafting_planning; changes_requested → outline | outline |
| drafting_planning | produce | executor | Проектирование ближайшего исполнения | complete → drafting_planning_inspection | drafting_planning |
| drafting_planning_inspection | inspect | reviewer | Проверка результата | clear → drafting; changes_requested → drafting_planning | drafting_planning |
| drafting | produce | executor | Исполнение согласованного плана | complete → validation | drafting |
| validation | check | executor | Получение и оценка фактов | satisfied → inspection; not_satisfied → drafting; inconclusive → drafting | validation |
| inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → drafting | drafting |


## task_planning

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Проектирование ближайшего исполнения | complete → framing_inspection | framing |
| framing_inspection | inspect | reviewer | Проверка результата | clear → classification; changes_requested → framing | framing |
| classification | produce | executor | Исполнение согласованного плана | complete → classification_inspection | classification |
| classification_inspection | inspect | reviewer | Проверка результата | clear → specification_planning; changes_requested → classification | classification |
| specification_planning | produce | executor | Проектирование ближайшего исполнения | complete → specification_planning_inspection | specification_planning |
| specification_planning_inspection | inspect | reviewer | Проверка результата | clear → requirements; changes_requested → specification_planning | specification_planning |
| requirements | produce | executor | Исполнение согласованного плана | complete → acceptance_design | requirements |
| acceptance_design | produce | executor | Исполнение согласованного плана | complete → work_planning | acceptance_design |
| work_planning | produce | executor | Исполнение согласованного плана | complete → verification_planning | work_planning |
| verification_planning | produce | executor | Исполнение согласованного плана | complete → task_inspection | verification_planning |
| task_inspection | inspect | reviewer | Проверка результата | clear → publication_planning; changes_requested → requirements | requirements |
| publication_planning | produce | executor | Проектирование ближайшего исполнения | complete → publication_planning_inspection | publication_planning |
| publication_planning_inspection | inspect | reviewer | Проверка результата | clear → publication; changes_requested → publication_planning | publication_planning |
| publication | publish | executor | Механический шаг процесса | complete → publication_result | — |
| publication_result | produce | executor | Исполнение согласованного плана | complete → publication_inspection | publication_result |
| publication_inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → publication_result | publication_result |


## sprint_planning

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Проектирование ближайшего исполнения | complete → framing_inspection | framing |
| framing_inspection | inspect | reviewer | Проверка результата | clear → decomposition; changes_requested → framing | framing |
| decomposition | produce | executor | Исполнение согласованного плана | complete → task_classification | decomposition |
| task_classification | produce | executor | Исполнение согласованного плана | complete → decomposition_inspection | task_classification |
| decomposition_inspection | inspect | reviewer | Проверка результата | clear → specification_planning; changes_requested → decomposition | decomposition |
| specification_planning | produce | executor | Проектирование ближайшего исполнения | complete → specification_planning_inspection | specification_planning |
| specification_planning_inspection | inspect | reviewer | Проверка результата | clear → task_contracts; changes_requested → specification_planning | specification_planning |
| task_contracts | produce | executor | Исполнение согласованного плана | complete → dependency_design | task_contracts |
| dependency_design | produce | executor | Исполнение согласованного плана | complete → execution_planning | dependency_design |
| execution_planning | produce | executor | Исполнение согласованного плана | complete → coverage_analysis | execution_planning |
| coverage_analysis | check | executor | Получение и оценка фактов | satisfied → sprint_inspection; not_satisfied → task_contracts; inconclusive → task_contracts | coverage_analysis |
| sprint_inspection | inspect | reviewer | Проверка результата | clear → publication_planning; changes_requested → task_contracts | task_contracts |
| publication_planning | produce | executor | Проектирование ближайшего исполнения | complete → publication_planning_inspection | publication_planning |
| publication_planning_inspection | inspect | reviewer | Проверка результата | clear → publication; changes_requested → publication_planning | publication_planning |
| publication | publish | executor | Механический шаг процесса | complete → publication_result | — |
| publication_result | produce | executor | Исполнение согласованного плана | complete → publication_inspection | publication_result |
| publication_inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → publication_result | publication_result |


## integration

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Проектирование ближайшего исполнения | complete → framing_inspection | framing |
| framing_inspection | inspect | reviewer | Проверка результата | clear → integration_planning; changes_requested → framing | framing |
| integration_planning | produce | executor | Проектирование ближайшего исполнения | complete → integration_planning_inspection | integration_planning |
| integration_planning_inspection | inspect | reviewer | Проверка результата | clear → integration; changes_requested → integration_planning | integration_planning |
| integration | apply_plan | executor | Исполнение согласованного плана | complete → verification | integration |
| verification | check | executor | Получение и оценка фактов | satisfied → inspection; not_satisfied → integration; inconclusive → integration | verification |
| inspection | inspect | reviewer | Проверка результата | clear → publication_planning; changes_requested → integration | integration |
| publication_planning | produce | executor | Проектирование ближайшего исполнения | complete → publication_planning_inspection | publication_planning |
| publication_planning_inspection | inspect | reviewer | Проверка результата | clear → publication; changes_requested → publication_planning | publication_planning |
| publication | publish | executor | Механический шаг процесса | complete → publication_result | — |
| publication_result | produce | executor | Исполнение согласованного плана | complete → publication_inspection | publication_result |
| publication_inspection | inspect | reviewer | Проверка результата | clear → завершение; changes_requested → publication_result | publication_result |


## Именованные feedback-ребра

| ID | Тип | Из этапа | В этап |
|---|---|---|---|
| FB-DEV-01 | development | test_inspection | test_implementation |
| FB-DEV-03 | development | implementation_inspection | implementation |
| FB-TST-01 | test_development | test_inspection | test_implementation |
| FB-VER-01 | verification | planning_inspection | planning |
| FB-VER-02 | verification | execution_inspection | execution |
| FB-REV-ERP-01 | review | planning_inspection | planning |
| FB-REV-ERP-02 | review | execution_inspection | inspection |
| FB-DES-ERP-01 | design | framing_inspection | framing |
| FB-DES-ERP-02 | design | alternatives_inspection | alternatives |
| FB-DES-ERP-03 | design | decision_inspection | decision |
| FB-DES-ERP-04 | design | design_inspection | detailed_design |
| FB-ANA-ERP-01 | analysis | framing_inspection | framing |
| FB-ANA-ERP-02 | analysis | evidence_collection_inspection | evidence_collection |
| FB-ANA-ERP-03 | analysis | analysis_planning_inspection | analysis_planning |
| FB-ANA-ERP-04 | analysis | analysis_inspection | reasoning |
| FB-PRO-ERP-01 | profiling | framing_inspection | framing |
| FB-PRO-ERP-02 | profiling | baseline_inspection | baseline_measurement |
| FB-PRO-ERP-03 | profiling | experiment_planning_inspection | experiment_planning |
| FB-PRO-ERP-04 | profiling | measurement_inspection | measurement |
| FB-PRO-ERP-05 | profiling | analysis_planning_inspection | analysis_planning |
| FB-PRO-ERP-06 | profiling | profiling_inspection | analysis |
| FB-DIA-ERP-01 | environment_diagnostics | framing_inspection | framing |
| FB-DIA-ERP-02 | environment_diagnostics | reproduction_inspection | reproduction |
| FB-DIA-ERP-03 | environment_diagnostics | hypothesis_planning_inspection | hypothesis_planning |
| FB-DIA-ERP-04 | environment_diagnostics | experiments_inspection | experiments |
| FB-DIA-ERP-05 | environment_diagnostics | analysis_planning_inspection | analysis_planning |
| FB-DIA-ERP-06 | environment_diagnostics | diagnostics_inspection | root_cause_analysis |
| FB-ENV-ERP-01 | environment_remediation | framing_inspection | framing |
| FB-ENV-ERP-02 | environment_remediation | baseline_inspection | baseline |
| FB-ENV-ERP-03 | environment_remediation | change_planning_inspection | change_planning |
| FB-ENV-ERP-04 | environment_remediation | inspection | application |
| FB-DOC-ERP-01 | documentation | framing_inspection | framing |
| FB-DOC-ERP-02 | documentation | outline_inspection | outline |
| FB-DOC-ERP-03 | documentation | drafting_planning_inspection | drafting_planning |
| FB-DOC-ERP-04 | documentation | inspection | drafting |
| FB-TPL-ERP-01 | task_planning | framing_inspection | framing |
| FB-TPL-ERP-02 | task_planning | classification_inspection | classification |
| FB-TPL-ERP-03 | task_planning | specification_planning_inspection | specification_planning |
| FB-TPL-ERP-04 | task_planning | task_inspection | requirements |
| FB-TPL-ERP-05 | task_planning | publication_planning_inspection | publication_planning |
| FB-TPL-ERP-06 | task_planning | publication_inspection | publication_result |
| FB-SPL-ERP-01 | sprint_planning | framing_inspection | framing |
| FB-SPL-ERP-02 | sprint_planning | decomposition_inspection | decomposition |
| FB-SPL-ERP-03 | sprint_planning | specification_planning_inspection | specification_planning |
| FB-SPL-ERP-04 | sprint_planning | sprint_inspection | task_contracts |
| FB-SPL-ERP-05 | sprint_planning | publication_planning_inspection | publication_planning |
| FB-SPL-ERP-06 | sprint_planning | publication_inspection | publication_result |
| FB-INT-ERP-01 | integration | framing_inspection | framing |
| FB-INT-ERP-02 | integration | integration_planning_inspection | integration_planning |
| FB-INT-ERP-03 | integration | inspection | integration |
| FB-INT-ERP-04 | integration | publication_planning_inspection | publication_planning |
| FB-INT-ERP-05 | integration | publication_inspection | publication_result |
