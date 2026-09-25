# Карта исполняемого каталога DDD-09

Обновлено: **2026-09-24**.

Семантика и границы: [process-catalogue](process-catalogue.md).

Колонка «Роль» — каноническая карта ownership для действующего каталога. Каждый этап имеет
ровно одну явно сохранённую `role`; в этих пресетах это `executor` или `reviewer`.
Другие процессы могут задавать свои роли и одноролевую самопроверку. Роль не выводится автоматически из обработчика,
статуса, `read_only` или наличия теста. `observe`, `produce`, `check`, `apply_plan`, `revise`
и `publish` относятся к исполнителю, если строка не принадлежит goal type `review`;
независимые `inspect`-этапы относятся к ревьюеру. Исключение `self_inspection` вне `review`
явно остаётся самопроверкой исполнителя. Весь goal type `review`, включая `planning` и
`self_inspection`, принадлежит ревьюеру. `publish` обозначает роль исполнителя, но не заменяет
отдельное полномочие на приёмку, публикацию или интеграцию. Семантика поручений и переходов
между ролями определена в [правилах разработки](../governance/development-rules.md#роли-этапов-и-непрерывность-поручения).

## Черновик назначений навыков этапа

Планируемое дополнение шаблона этапа — метанавыки и предметные навыки, которые
постановщик уточняет при создании Task. Это **не реализованные поля** текущего
каталога и не новые роли: карта ниже по-прежнему содержит `executor`/`reviewer`.
См. [два набора навыков](../workflows/task-stage-skills-draft.md#два-набора-навыков-у-каждого-этапа)
и [отложенные решения по tooling](../workflows/task-stage-skills-draft.md#что-существует-сейчас-и-что-отложено).

## development

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| baseline | observe | executor | Сбор фактов для исполнительского результата | complete → solution_planning | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| solution_planning | produce | executor | Создание исполнительского результата | complete → verification_planning | solution_planning, baseline, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| verification_planning | produce | executor | Создание исполнительского результата | complete → test_implementation | verification_planning, baseline, solution_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| test_implementation | produce | executor | Создание исполнительского результата | complete → test_inspection | test_implementation, baseline, solution_planning, verification_planning, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| test_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → test_remediation; clear → implementation | test_inspection, test_remediation, baseline, solution_planning, verification_planning, test_implementation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| test_remediation | revise | executor | Изменение или исправление результата исполнителем | complete → test_remediation_inspection | test_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| test_remediation_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → test_remediation; clear → implementation | test_remediation_inspection, test_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| implementation | produce | executor | Создание исполнительского результата | complete → implementation_inspection | implementation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| implementation_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → implementation_remediation; clear → documentation | implementation_inspection, implementation_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_remediation_inspection, documentation |
| implementation_remediation | revise | executor | Изменение или исправление результата исполнителем | complete → implementation_remediation_inspection | implementation_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation_inspection, documentation |
| implementation_remediation_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → implementation_remediation; clear → documentation | implementation_remediation_inspection, implementation_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, documentation |
| documentation | produce | executor | Создание исполнительского результата | complete → завершение | documentation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection |

## test_development

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| baseline | observe | executor | Сбор фактов для исполнительского результата | complete → coverage_planning | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |
| coverage_planning | produce | executor | Создание исполнительского результата | complete → test_planning | coverage_planning, baseline, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |
| test_planning | produce | executor | Создание исполнительского результата | complete → test_implementation | test_planning, baseline, coverage_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |
| test_implementation | produce | executor | Создание исполнительского результата | complete → sensitivity_verification | test_implementation, baseline, coverage_planning, test_planning, sensitivity_verification, test_inspection, remediation, remediation_inspection |
| sensitivity_verification | check | executor | Обязательная проверка внутри исполнительского участка | inconclusive → test_implementation; not_satisfied → test_implementation; satisfied → test_inspection | sensitivity_verification, baseline, coverage_planning, test_planning, test_implementation, test_inspection, remediation, remediation_inspection |
| test_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → завершение | test_inspection, remediation, baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, remediation_inspection |
| remediation | revise | executor | Изменение или исправление результата исполнителем | complete → remediation_inspection | remediation, baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation_inspection |
| remediation_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → завершение | remediation_inspection, remediation, baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection |

## verification

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| planning | produce | executor | Создание исполнительского результата | complete → execution | planning, execution, analysis, self_inspection |
| execution | check | executor | Обязательная проверка внутри исполнительского участка | inconclusive → analysis; not_satisfied → analysis; satisfied → analysis | execution, planning, analysis, self_inspection |
| analysis | produce | executor | Создание исполнительского результата | complete → self_inspection | analysis, planning, execution, self_inspection |
| self_inspection | inspect | executor | Локальная самопроверка результата исполнителя, не независимое ревью | changes_requested → planning; clear → завершение | self_inspection, planning, execution, analysis |

## review

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| planning | produce | reviewer | Весь review-маршрут, включая подготовку и самопроверку, выполняет назначенный ревьюер | complete → inspection | planning, inspection, self_inspection |
| inspection | inspect | reviewer | Весь review-маршрут, включая подготовку и самопроверку, выполняет назначенный ревьюер | changes_requested → self_inspection; clear → self_inspection | inspection, planning, self_inspection |
| self_inspection | inspect | reviewer | Весь review-маршрут, включая подготовку и самопроверку, выполняет назначенный ревьюер | changes_requested → inspection; clear → завершение | self_inspection, inspection, planning |

## design

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Создание исполнительского результата | complete → alternatives | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |
| alternatives | produce | executor | Создание исполнительского результата | complete → decision | alternatives, framing, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |
| decision | produce | executor | Создание исполнительского результата | complete → detailed_design | decision, framing, alternatives, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |
| detailed_design | produce | executor | Создание исполнительского результата | complete → design_verification | detailed_design, framing, alternatives, decision, design_verification, design_inspection, remediation, remediation_inspection |
| design_verification | check | executor | Обязательная проверка внутри исполнительского участка | inconclusive → detailed_design; not_satisfied → detailed_design; satisfied → design_inspection | design_verification, framing, alternatives, decision, detailed_design, design_inspection, remediation, remediation_inspection |
| design_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → завершение | design_inspection, remediation, framing, alternatives, decision, detailed_design, design_verification, remediation_inspection |
| remediation | revise | executor | Изменение или исправление результата исполнителем | complete → remediation_inspection | remediation, framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation_inspection |
| remediation_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → завершение | remediation_inspection, remediation, framing, alternatives, decision, detailed_design, design_verification, design_inspection |

## analysis

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Создание исполнительского результата | complete → evidence_collection | framing, evidence_collection, reasoning, challenge, self_inspection |
| evidence_collection | observe | executor | Сбор фактов для исполнительского результата | complete → reasoning | evidence_collection, framing, reasoning, challenge, self_inspection |
| reasoning | produce | executor | Создание исполнительского результата | complete → challenge | reasoning, framing, evidence_collection, challenge, self_inspection |
| challenge | produce | executor | Создание исполнительского результата | complete → self_inspection | challenge, framing, evidence_collection, reasoning, self_inspection |
| self_inspection | inspect | executor | Локальная самопроверка результата исполнителя, не независимое ревью | changes_requested → evidence_collection; clear → завершение | self_inspection, evidence_collection, reasoning, challenge, framing |

## profiling

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Создание исполнительского результата | complete → baseline_measurement | framing, baseline_measurement, experiment_planning, measurement, analysis, confirmation, self_inspection |
| baseline_measurement | observe | executor | Сбор фактов для исполнительского результата | complete → experiment_planning | baseline_measurement, framing, experiment_planning, measurement, analysis, confirmation, self_inspection |
| experiment_planning | produce | executor | Создание исполнительского результата | complete → measurement | experiment_planning, framing, baseline_measurement, measurement, analysis, confirmation, self_inspection |
| measurement | observe | executor | Сбор фактов для исполнительского результата | complete → analysis | measurement, framing, baseline_measurement, experiment_planning, analysis, confirmation, self_inspection |
| analysis | produce | executor | Создание исполнительского результата | complete → confirmation | analysis, framing, baseline_measurement, experiment_planning, measurement, confirmation, self_inspection |
| confirmation | check | executor | Обязательная проверка внутри исполнительского участка | inconclusive → self_inspection; not_satisfied → self_inspection; satisfied → self_inspection | confirmation, framing, baseline_measurement, experiment_planning, measurement, analysis, self_inspection |
| self_inspection | inspect | executor | Локальная самопроверка результата исполнителя, не независимое ревью | changes_requested → experiment_planning; clear → завершение | self_inspection, experiment_planning, measurement, analysis, confirmation, framing, baseline_measurement |

## environment_diagnostics

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Создание исполнительского результата | complete → reproduction | framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |
| reproduction | observe | executor | Сбор фактов для исполнительского результата | complete → hypothesis_planning | reproduction, framing, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |
| hypothesis_planning | produce | executor | Создание исполнительского результата | complete → experiments | hypothesis_planning, framing, reproduction, experiments, root_cause_analysis, confirmation, self_inspection |
| experiments | observe | executor | Сбор фактов для исполнительского результата | complete → root_cause_analysis | experiments, framing, reproduction, hypothesis_planning, root_cause_analysis, confirmation, self_inspection |
| root_cause_analysis | produce | executor | Создание исполнительского результата | complete → confirmation | root_cause_analysis, framing, reproduction, hypothesis_planning, experiments, confirmation, self_inspection |
| confirmation | check | executor | Обязательная проверка внутри исполнительского участка | inconclusive → self_inspection; not_satisfied → self_inspection; satisfied → self_inspection | confirmation, framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, self_inspection |
| self_inspection | inspect | executor | Локальная самопроверка результата исполнителя, не независимое ревью | changes_requested → reproduction; clear → завершение | self_inspection, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, framing |

## environment_remediation

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Создание исполнительского результата | complete → baseline | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |
| baseline | observe | executor | Сбор фактов для исполнительского результата | complete → change_planning | baseline, framing, change_planning, application, verification, inspection, correction, correction_inspection |
| change_planning | produce | executor | Создание исполнительского результата | complete → application | change_planning, framing, baseline, application, verification, inspection, correction, correction_inspection |
| application | apply_plan | executor | Изменение или исправление результата исполнителем | complete → verification | application, framing, baseline, change_planning, verification, inspection, correction, correction_inspection |
| verification | check | executor | Обязательная проверка внутри исполнительского участка | inconclusive → inspection; not_satisfied → inspection; satisfied → inspection | verification, framing, baseline, change_planning, application, inspection, correction, correction_inspection |
| inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → correction; clear → завершение | inspection, correction, framing, baseline, change_planning, application, verification, correction_inspection |
| correction | apply_plan | executor | Изменение или исправление результата исполнителем | complete → correction_inspection | correction, framing, baseline, change_planning, application, verification, inspection, correction_inspection |
| correction_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → correction; clear → завершение | correction_inspection, correction, framing, baseline, change_planning, application, verification, inspection |

## documentation

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Создание исполнительского результата | complete → outline | framing, outline, drafting, validation, inspection, remediation, remediation_inspection |
| outline | produce | executor | Создание исполнительского результата | complete → drafting | outline, framing, drafting, validation, inspection, remediation, remediation_inspection |
| drafting | produce | executor | Создание исполнительского результата | complete → validation | drafting, framing, outline, validation, inspection, remediation, remediation_inspection |
| validation | check | executor | Обязательная проверка внутри исполнительского участка | inconclusive → drafting; not_satisfied → drafting; satisfied → inspection | validation, framing, outline, drafting, inspection, remediation, remediation_inspection |
| inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → завершение | inspection, remediation, framing, outline, drafting, validation, remediation_inspection |
| remediation | revise | executor | Изменение или исправление результата исполнителем | complete → remediation_inspection | remediation, framing, outline, drafting, validation, inspection, remediation_inspection |
| remediation_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → завершение | remediation_inspection, remediation, framing, outline, drafting, validation, inspection |

## task_planning

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Создание исполнительского результата | complete → classification | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |
| classification | produce | executor | Создание исполнительского результата | complete → requirements | classification, framing, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |
| requirements | produce | executor | Создание исполнительского результата | complete → acceptance_design | requirements, framing, classification, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |
| acceptance_design | produce | executor | Создание исполнительского результата | complete → work_planning | acceptance_design, framing, classification, requirements, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |
| work_planning | produce | executor | Создание исполнительского результата | complete → verification_planning | work_planning, framing, classification, requirements, acceptance_design, verification_planning, task_inspection, remediation, remediation_inspection |
| verification_planning | produce | executor | Создание исполнительского результата | complete → task_inspection | verification_planning, framing, classification, requirements, acceptance_design, work_planning, task_inspection, remediation, remediation_inspection |
| task_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → publication | task_inspection, remediation, framing, classification, requirements, acceptance_design, work_planning, verification_planning, remediation_inspection |
| remediation | revise | executor | Изменение или исправление результата исполнителем | complete → remediation_inspection | remediation, framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation_inspection |
| remediation_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → publication | remediation_inspection, remediation, framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection |
| publication | publish | executor | Исполнительская публикация после отдельного полномочия | complete → завершение | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

## sprint_planning

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Создание исполнительского результата | complete → decomposition | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| decomposition | produce | executor | Создание исполнительского результата | complete → task_classification | decomposition, framing, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| task_classification | produce | executor | Создание исполнительского результата | complete → task_contracts | task_classification, framing, decomposition, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| task_contracts | produce | executor | Создание исполнительского результата | complete → dependency_design | task_contracts, framing, decomposition, task_classification, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| dependency_design | produce | executor | Создание исполнительского результата | complete → execution_planning | dependency_design, framing, decomposition, task_classification, task_contracts, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| execution_planning | produce | executor | Создание исполнительского результата | complete → coverage_analysis | execution_planning, framing, decomposition, task_classification, task_contracts, dependency_design, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| coverage_analysis | check | executor | Обязательная проверка внутри исполнительского участка | inconclusive → task_contracts; not_satisfied → task_contracts; satisfied → sprint_inspection | coverage_analysis, framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, sprint_inspection, remediation, remediation_inspection |
| sprint_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → publication | sprint_inspection, remediation, framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, remediation_inspection |
| remediation | revise | executor | Изменение или исправление результата исполнителем | complete → remediation_inspection | remediation, framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation_inspection |
| remediation_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → publication | remediation_inspection, remediation, framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection |
| publication | publish | executor | Исполнительская публикация после отдельного полномочия | complete → завершение | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

## integration

| Этап | Обработчик | Роль | Обоснование | Основной переход | Возвраты |
|---|---|---|---|---|---|
| framing | produce | executor | Создание исполнительского результата | complete → integration_planning | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |
| integration_planning | produce | executor | Создание исполнительского результата | complete → integration | integration_planning, framing, integration, verification, inspection, remediation, remediation_inspection |
| integration | apply_plan | executor | Изменение или исправление результата исполнителем | complete → verification | integration, framing, integration_planning, verification, inspection, remediation, remediation_inspection |
| verification | check | executor | Обязательная проверка внутри исполнительского участка | inconclusive → inspection; not_satisfied → inspection; satisfied → inspection | verification, framing, integration_planning, integration, inspection, remediation, remediation_inspection |
| inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → publication | inspection, remediation, framing, integration_planning, integration, verification, remediation_inspection |
| remediation | revise | executor | Изменение или исправление результата исполнителем | complete → remediation_inspection | remediation, framing, integration_planning, integration, verification, inspection, remediation_inspection |
| remediation_inspection | inspect | reviewer | Независимый осмотр результата или исправления | changes_requested → remediation; clear → publication | remediation_inspection, remediation, framing, integration_planning, integration, verification, inspection |
| publication | publish | executor | Исполнительская публикация после отдельного полномочия | complete → завершение | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |

## Именованные feedback-ребра

| ID | Тип | Из этапа | В этап |
|---|---|---|---|
| FB-ANA-01 | analysis | self_inspection | evidence_collection |
| FB-ANA-02 | analysis | self_inspection | reasoning |
| FB-ANA-03 | analysis | self_inspection | challenge |
| FB-DES-01 | design | design_inspection | remediation |
| FB-DES-02 | design | remediation_inspection | remediation |
| FB-DEV-01 | development | test_inspection | test_remediation |
| FB-DEV-02 | development | test_remediation_inspection | test_remediation |
| FB-DEV-03 | development | implementation_inspection | implementation_remediation |
| FB-DEV-04 | development | implementation_remediation_inspection | implementation_remediation |
| FB-DIA-01 | environment_diagnostics | self_inspection | reproduction |
| FB-DIA-02 | environment_diagnostics | self_inspection | hypothesis_planning |
| FB-DIA-03 | environment_diagnostics | self_inspection | experiments |
| FB-DIA-04 | environment_diagnostics | self_inspection | root_cause_analysis |
| FB-DIA-05 | environment_diagnostics | self_inspection | confirmation |
| FB-DOC-01 | documentation | inspection | remediation |
| FB-DOC-02 | documentation | remediation_inspection | remediation |
| FB-ENV-01 | environment_remediation | inspection | correction |
| FB-ENV-02 | environment_remediation | correction_inspection | correction |
| FB-INT-01 | integration | inspection | remediation |
| FB-INT-02 | integration | remediation_inspection | remediation |
| FB-PRO-01 | profiling | self_inspection | experiment_planning |
| FB-PRO-02 | profiling | self_inspection | measurement |
| FB-PRO-03 | profiling | self_inspection | analysis |
| FB-PRO-04 | profiling | self_inspection | confirmation |
| FB-REV-01 | review | self_inspection | inspection |
| FB-SPL-01 | sprint_planning | sprint_inspection | remediation |
| FB-SPL-02 | sprint_planning | remediation_inspection | remediation |
| FB-TPL-01 | task_planning | task_inspection | remediation |
| FB-TPL-02 | task_planning | remediation_inspection | remediation |
| FB-TST-01 | test_development | test_inspection | remediation |
| FB-TST-02 | test_development | remediation_inspection | remediation |
| FB-VER-01 | verification | self_inspection | planning |
| FB-VER-02 | verification | self_inspection | execution |
| FB-VER-03 | verification | self_inspection | analysis |
