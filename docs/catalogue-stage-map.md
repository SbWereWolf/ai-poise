# Карта исполняемого каталога DDD-09

Обновлено: **2026-09-07T07:34:23+05:00**.

Семантика и границы: [process-catalogue](process-catalogue.md).

## development

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| baseline | observe | complete → solution_planning | baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| solution_planning | produce | complete → verification_planning | solution_planning, baseline, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| verification_planning | produce | complete → test_implementation | verification_planning, baseline, solution_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| test_implementation | produce | complete → test_inspection | test_implementation, baseline, solution_planning, verification_planning, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| test_inspection | inspect | changes_requested → test_remediation; clear → implementation | test_inspection, test_remediation, baseline, solution_planning, verification_planning, test_implementation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| test_remediation | revise | complete → test_remediation_inspection | test_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| test_remediation_inspection | inspect | changes_requested → test_remediation; clear → implementation | test_remediation_inspection, test_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| implementation | produce | complete → implementation_inspection | implementation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation_inspection, implementation_remediation, implementation_remediation_inspection, documentation |
| implementation_inspection | inspect | changes_requested → implementation_remediation; clear → documentation | implementation_inspection, implementation_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_remediation_inspection, documentation |
| implementation_remediation | revise | complete → implementation_remediation_inspection | implementation_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation_inspection, documentation |
| implementation_remediation_inspection | inspect | changes_requested → implementation_remediation; clear → documentation | implementation_remediation_inspection, implementation_remediation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, documentation |
| documentation | produce | complete → завершение | documentation, baseline, solution_planning, verification_planning, test_implementation, test_inspection, test_remediation, test_remediation_inspection, implementation, implementation_inspection, implementation_remediation, implementation_remediation_inspection |

## test_development

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| baseline | observe | complete → coverage_planning | baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |
| coverage_planning | produce | complete → test_planning | coverage_planning, baseline, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |
| test_planning | produce | complete → test_implementation | test_planning, baseline, coverage_planning, test_implementation, sensitivity_verification, test_inspection, remediation, remediation_inspection |
| test_implementation | produce | complete → sensitivity_verification | test_implementation, baseline, coverage_planning, test_planning, sensitivity_verification, test_inspection, remediation, remediation_inspection |
| sensitivity_verification | check | inconclusive → test_implementation; not_satisfied → test_implementation; satisfied → test_inspection | sensitivity_verification, baseline, coverage_planning, test_planning, test_implementation, test_inspection, remediation, remediation_inspection |
| test_inspection | inspect | changes_requested → remediation; clear → завершение | test_inspection, remediation, baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, remediation_inspection |
| remediation | revise | complete → remediation_inspection | remediation, baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection, remediation_inspection |
| remediation_inspection | inspect | changes_requested → remediation; clear → завершение | remediation_inspection, remediation, baseline, coverage_planning, test_planning, test_implementation, sensitivity_verification, test_inspection |

## verification

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| planning | produce | complete → execution | planning, execution, analysis, self_inspection |
| execution | check | inconclusive → analysis; not_satisfied → analysis; satisfied → analysis | execution, planning, analysis, self_inspection |
| analysis | produce | complete → self_inspection | analysis, planning, execution, self_inspection |
| self_inspection | inspect | changes_requested → planning; clear → завершение | self_inspection, planning, execution, analysis |

## review

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| planning | produce | complete → inspection | planning, inspection, self_inspection |
| inspection | inspect | changes_requested → self_inspection; clear → self_inspection | inspection, planning, self_inspection |
| self_inspection | inspect | changes_requested → inspection; clear → завершение | self_inspection, inspection, planning |

## design

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| framing | produce | complete → alternatives | framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |
| alternatives | produce | complete → decision | alternatives, framing, decision, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |
| decision | produce | complete → detailed_design | decision, framing, alternatives, detailed_design, design_verification, design_inspection, remediation, remediation_inspection |
| detailed_design | produce | complete → design_verification | detailed_design, framing, alternatives, decision, design_verification, design_inspection, remediation, remediation_inspection |
| design_verification | check | inconclusive → detailed_design; not_satisfied → detailed_design; satisfied → design_inspection | design_verification, framing, alternatives, decision, detailed_design, design_inspection, remediation, remediation_inspection |
| design_inspection | inspect | changes_requested → remediation; clear → завершение | design_inspection, remediation, framing, alternatives, decision, detailed_design, design_verification, remediation_inspection |
| remediation | revise | complete → remediation_inspection | remediation, framing, alternatives, decision, detailed_design, design_verification, design_inspection, remediation_inspection |
| remediation_inspection | inspect | changes_requested → remediation; clear → завершение | remediation_inspection, remediation, framing, alternatives, decision, detailed_design, design_verification, design_inspection |

## analysis

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| framing | produce | complete → evidence_collection | framing, evidence_collection, reasoning, challenge, self_inspection |
| evidence_collection | observe | complete → reasoning | evidence_collection, framing, reasoning, challenge, self_inspection |
| reasoning | produce | complete → challenge | reasoning, framing, evidence_collection, challenge, self_inspection |
| challenge | produce | complete → self_inspection | challenge, framing, evidence_collection, reasoning, self_inspection |
| self_inspection | inspect | changes_requested → evidence_collection; clear → завершение | self_inspection, evidence_collection, reasoning, challenge, framing |

## profiling

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| framing | produce | complete → baseline_measurement | framing, baseline_measurement, experiment_planning, measurement, analysis, confirmation, self_inspection |
| baseline_measurement | observe | complete → experiment_planning | baseline_measurement, framing, experiment_planning, measurement, analysis, confirmation, self_inspection |
| experiment_planning | produce | complete → measurement | experiment_planning, framing, baseline_measurement, measurement, analysis, confirmation, self_inspection |
| measurement | observe | complete → analysis | measurement, framing, baseline_measurement, experiment_planning, analysis, confirmation, self_inspection |
| analysis | produce | complete → confirmation | analysis, framing, baseline_measurement, experiment_planning, measurement, confirmation, self_inspection |
| confirmation | check | inconclusive → self_inspection; not_satisfied → self_inspection; satisfied → self_inspection | confirmation, framing, baseline_measurement, experiment_planning, measurement, analysis, self_inspection |
| self_inspection | inspect | changes_requested → experiment_planning; clear → завершение | self_inspection, experiment_planning, measurement, analysis, confirmation, framing, baseline_measurement |

## environment_diagnostics

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| framing | produce | complete → reproduction | framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |
| reproduction | observe | complete → hypothesis_planning | reproduction, framing, hypothesis_planning, experiments, root_cause_analysis, confirmation, self_inspection |
| hypothesis_planning | produce | complete → experiments | hypothesis_planning, framing, reproduction, experiments, root_cause_analysis, confirmation, self_inspection |
| experiments | observe | complete → root_cause_analysis | experiments, framing, reproduction, hypothesis_planning, root_cause_analysis, confirmation, self_inspection |
| root_cause_analysis | produce | complete → confirmation | root_cause_analysis, framing, reproduction, hypothesis_planning, experiments, confirmation, self_inspection |
| confirmation | check | inconclusive → self_inspection; not_satisfied → self_inspection; satisfied → self_inspection | confirmation, framing, reproduction, hypothesis_planning, experiments, root_cause_analysis, self_inspection |
| self_inspection | inspect | changes_requested → reproduction; clear → завершение | self_inspection, reproduction, hypothesis_planning, experiments, root_cause_analysis, confirmation, framing |

## environment_remediation

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| framing | produce | complete → baseline | framing, baseline, change_planning, application, verification, inspection, correction, correction_inspection |
| baseline | observe | complete → change_planning | baseline, framing, change_planning, application, verification, inspection, correction, correction_inspection |
| change_planning | produce | complete → application | change_planning, framing, baseline, application, verification, inspection, correction, correction_inspection |
| application | apply_plan | complete → verification | application, framing, baseline, change_planning, verification, inspection, correction, correction_inspection |
| verification | check | inconclusive → inspection; not_satisfied → inspection; satisfied → inspection | verification, framing, baseline, change_planning, application, inspection, correction, correction_inspection |
| inspection | inspect | changes_requested → correction; clear → завершение | inspection, correction, framing, baseline, change_planning, application, verification, correction_inspection |
| correction | apply_plan | complete → correction_inspection | correction, framing, baseline, change_planning, application, verification, inspection, correction_inspection |
| correction_inspection | inspect | changes_requested → correction; clear → завершение | correction_inspection, correction, framing, baseline, change_planning, application, verification, inspection |

## documentation

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| framing | produce | complete → outline | framing, outline, drafting, validation, inspection, remediation, remediation_inspection |
| outline | produce | complete → drafting | outline, framing, drafting, validation, inspection, remediation, remediation_inspection |
| drafting | produce | complete → validation | drafting, framing, outline, validation, inspection, remediation, remediation_inspection |
| validation | check | inconclusive → drafting; not_satisfied → drafting; satisfied → inspection | validation, framing, outline, drafting, inspection, remediation, remediation_inspection |
| inspection | inspect | changes_requested → remediation; clear → завершение | inspection, remediation, framing, outline, drafting, validation, remediation_inspection |
| remediation | revise | complete → remediation_inspection | remediation, framing, outline, drafting, validation, inspection, remediation_inspection |
| remediation_inspection | inspect | changes_requested → remediation; clear → завершение | remediation_inspection, remediation, framing, outline, drafting, validation, inspection |

## task_planning

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| framing | produce | complete → classification | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |
| classification | produce | complete → requirements | classification, framing, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |
| requirements | produce | complete → acceptance_design | requirements, framing, classification, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |
| acceptance_design | produce | complete → work_planning | acceptance_design, framing, classification, requirements, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |
| work_planning | produce | complete → verification_planning | work_planning, framing, classification, requirements, acceptance_design, verification_planning, task_inspection, remediation, remediation_inspection |
| verification_planning | produce | complete → task_inspection | verification_planning, framing, classification, requirements, acceptance_design, work_planning, task_inspection, remediation, remediation_inspection |
| task_inspection | inspect | changes_requested → remediation; clear → publication | task_inspection, remediation, framing, classification, requirements, acceptance_design, work_planning, verification_planning, remediation_inspection |
| remediation | revise | complete → remediation_inspection | remediation, framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation_inspection |
| remediation_inspection | inspect | changes_requested → remediation; clear → publication | remediation_inspection, remediation, framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection |
| publication | publish | complete → завершение | framing, classification, requirements, acceptance_design, work_planning, verification_planning, task_inspection, remediation, remediation_inspection |

## sprint_planning

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| framing | produce | complete → decomposition | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| decomposition | produce | complete → task_classification | decomposition, framing, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| task_classification | produce | complete → task_contracts | task_classification, framing, decomposition, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| task_contracts | produce | complete → dependency_design | task_contracts, framing, decomposition, task_classification, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| dependency_design | produce | complete → execution_planning | dependency_design, framing, decomposition, task_classification, task_contracts, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| execution_planning | produce | complete → coverage_analysis | execution_planning, framing, decomposition, task_classification, task_contracts, dependency_design, coverage_analysis, sprint_inspection, remediation, remediation_inspection |
| coverage_analysis | check | inconclusive → task_contracts; not_satisfied → task_contracts; satisfied → sprint_inspection | coverage_analysis, framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, sprint_inspection, remediation, remediation_inspection |
| sprint_inspection | inspect | changes_requested → remediation; clear → publication | sprint_inspection, remediation, framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, remediation_inspection |
| remediation | revise | complete → remediation_inspection | remediation, framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation_inspection |
| remediation_inspection | inspect | changes_requested → remediation; clear → publication | remediation_inspection, remediation, framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection |
| publication | publish | complete → завершение | framing, decomposition, task_classification, task_contracts, dependency_design, execution_planning, coverage_analysis, sprint_inspection, remediation, remediation_inspection |

## integration

| Этап | Обработчик | Основной переход | Возвраты |
|---|---|---|---|
| framing | produce | complete → integration_planning | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |
| integration_planning | produce | complete → integration | integration_planning, framing, integration, verification, inspection, remediation, remediation_inspection |
| integration | apply_plan | complete → verification | integration, framing, integration_planning, verification, inspection, remediation, remediation_inspection |
| verification | check | inconclusive → inspection; not_satisfied → inspection; satisfied → inspection | verification, framing, integration_planning, integration, inspection, remediation, remediation_inspection |
| inspection | inspect | changes_requested → remediation; clear → publication | inspection, remediation, framing, integration_planning, integration, verification, remediation_inspection |
| remediation | revise | complete → remediation_inspection | remediation, framing, integration_planning, integration, verification, inspection, remediation_inspection |
| remediation_inspection | inspect | changes_requested → remediation; clear → publication | remediation_inspection, remediation, framing, integration_planning, integration, verification, inspection |
| publication | publish | complete → завершение | framing, integration_planning, integration, verification, inspection, remediation, remediation_inspection |

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
