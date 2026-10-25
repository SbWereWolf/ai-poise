# Соответствие исходных этапов DDD-библиотекам

Создано: **2026-09-06T16:04:53+05:00**.

Карта возникла из исторического сопоставления 98 узлов семи семейств обработчиков.
Раздел `development` теперь содержит 16 обычных этапов: восемь сохранили исходные
`GS-DEV-*` IDs, восемь используют существующие `source_id` справочника. Четыре записи
удалённых отдельных этапов исправления исключены. Остальные 86 записей сохранены.

Всего в этой описательной карте 102 записи при 143 узлах текущего справочника.
Для `development` соответствие полное; 41 отсутствующее сопоставление относится к другим
типам задач. В `coverage()` поле `unmapped` означает записи карты без узла справочника,
а `extra` — узлы справочника без записи карты. После синхронизации `development`
`unmapped=[]`, а эти 41 узел остаются в `extra`. Статус карты
`DESCRIPTIVE_MAPPING_NOT_EXECUTED` сохраняется: сопоставление описывает ответственность,
исполнение сценариев подтверждают отдельные проверки.

В строках `development` указан текущий вход `Task.submit` и чистая функция
`evaluate_stage`: она вычисляет результат обработчика и объединяет область замечаний.
Сохранение жизненного цикла и принятие feedback остаются у Task, в том числе
`Task.mark_verified`. Конкретные классы расположены в `poise.modules.workflow.handlers`.

Прямой SQL и самостоятельный переход на следующий этап запрещены всем handlers.
Конкретный publisher у трёх mechanical узлов выбирается явной ссылкой в route config.
Общие services runner не копируются в обработчики.

| Goal type | Stage ID / этап | Handler | Библиотеки | Доменный/прикладной вход |
|---|---|---|---|---|
| `analysis` | `GS-ANA-01` · framing | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `analysis` | `GS-ANA-02` · evidence_collection | `observe` | `executions`, `artifacts`, `verification` | `Task.attach_observations` |
| `analysis` | `GS-ANA-03` · reasoning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `analysis` | `GS-ANA-04` · challenge | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `analysis` | `GS-ANA-05` · self_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `design` | `GS-DES-01` · framing | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `design` | `GS-DES-02` · alternatives | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `design` | `GS-DES-03` · decision | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `design` | `GS-DES-04` · detailed_design | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `design` | `GS-DES-05` · design_verification | `check` | `verification`, `executions`, `artifacts` | `Task.attach_observations + GateAssessment` |
| `design` | `GS-DES-06` · design_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `design` | `GS-DES-07` · remediation | `revise` | `tasks`, `content`, `verification`, `executions` | `Task.propose_resolution + Task.apply_submission` |
| `design` | `GS-DES-08` · remediation_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `development` | `GS-DEV-01` · baseline | `observe` | `executions`, `artifacts`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `PAIRED-20261003-baseline_inspection` · baseline_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `GS-DEV-02` · solution_planning | `produce` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `PAIRED-20261003-solution_planning_inspection` · solution_planning_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `GS-DEV-03` · verification_planning | `produce` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `PAIRED-20261003-verification_planning_inspection` · verification_planning_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `GS-DEV-04` · test_implementation | `produce` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `GS-DEV-05` · test_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `PAIRED-20261003-implementation_design` · implementation_design | `produce` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `PAIRED-20261003-implementation_design_inspection` · implementation_design_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `GS-DEV-08` · implementation | `produce` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `GS-DEV-09` · implementation_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `PAIRED-20261003-documentation_planning` · documentation_planning | `produce` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `PAIRED-20261003-documentation_planning_inspection` · documentation_planning_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `GS-DEV-12` · documentation | `produce` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `development` | `PAIRED-20261003-documentation_inspection` · documentation_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.submit + evaluate_stage` |
| `documentation` | `GS-DOC-01` · framing | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `documentation` | `GS-DOC-02` · outline | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `documentation` | `GS-DOC-03` · drafting | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `documentation` | `GS-DOC-04` · validation | `check` | `verification`, `executions`, `artifacts` | `Task.attach_observations + GateAssessment` |
| `documentation` | `GS-DOC-05` · inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `documentation` | `GS-DOC-06` · remediation | `revise` | `tasks`, `content`, `verification`, `executions` | `Task.propose_resolution + Task.apply_submission` |
| `documentation` | `GS-DOC-07` · remediation_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `environment_diagnostics` | `GS-DIA-01` · framing | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `environment_diagnostics` | `GS-DIA-02` · reproduction | `observe` | `executions`, `artifacts`, `verification` | `Task.attach_observations` |
| `environment_diagnostics` | `GS-DIA-03` · hypothesis_planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `environment_diagnostics` | `GS-DIA-04` · experiments | `observe` | `executions`, `artifacts`, `verification` | `Task.attach_observations` |
| `environment_diagnostics` | `GS-DIA-05` · root_cause_analysis | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `environment_diagnostics` | `GS-DIA-06` · confirmation | `check` | `verification`, `executions`, `artifacts` | `Task.attach_observations + GateAssessment` |
| `environment_diagnostics` | `GS-DIA-07` · self_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `environment_remediation` | `GS-ENV-01` · framing | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `environment_remediation` | `GS-ENV-02` · baseline | `observe` | `executions`, `artifacts`, `verification` | `Task.attach_observations` |
| `environment_remediation` | `GS-ENV-03` · change_planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `environment_remediation` | `GS-ENV-04` · application | `apply_plan` | `executions`, `workspaces`, `artifacts`, `verification` | `Operation.record_step + Task.attach_observations` |
| `environment_remediation` | `GS-ENV-05` · verification | `check` | `verification`, `executions`, `artifacts` | `Task.attach_observations + GateAssessment` |
| `environment_remediation` | `GS-ENV-06` · inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `environment_remediation` | `GS-ENV-07` · correction | `revise` | `tasks`, `content`, `verification`, `executions` | `Task.propose_resolution + Task.apply_submission` |
| `environment_remediation` | `GS-ENV-08` · correction_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `integration` | `GS-INT-01` · framing | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `integration` | `GS-INT-02` · integration_planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `integration` | `GS-INT-03` · integration | `apply_plan` | `executions`, `workspaces`, `artifacts`, `verification` | `Operation.record_step + Task.attach_observations` |
| `integration` | `GS-INT-04` · verification | `check` | `verification`, `executions`, `artifacts` | `Task.attach_observations + GateAssessment` |
| `integration` | `GS-INT-05` · inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `integration` | `GS-INT-06` · remediation | `revise` | `tasks`, `content`, `verification`, `executions` | `Task.propose_resolution + Task.apply_submission` |
| `integration` | `GS-INT-07` · remediation_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `integration` | `GS-INT-08` · publication | `publish` | `tasks`, `sprints`, `workspaces`, `operations` | `Owner.publish through PublicationCoordinator` |
| `profiling` | `GS-PRO-01` · framing | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `profiling` | `GS-PRO-02` · baseline_measurement | `observe` | `executions`, `artifacts`, `verification` | `Task.attach_observations` |
| `profiling` | `GS-PRO-03` · experiment_planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `profiling` | `GS-PRO-04` · measurement | `observe` | `executions`, `artifacts`, `verification` | `Task.attach_observations` |
| `profiling` | `GS-PRO-05` · analysis | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `profiling` | `GS-PRO-06` · confirmation | `check` | `verification`, `executions`, `artifacts` | `Task.attach_observations + GateAssessment` |
| `profiling` | `GS-PRO-07` · self_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `review` | `GS-REV-01` · planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `review` | `GS-REV-02` · inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `review` | `GS-REV-03` · self_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `sprint_planning` | `GS-SPL-01` · framing | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `sprint_planning` | `GS-SPL-02` · decomposition | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `sprint_planning` | `GS-SPL-03` · task_classification | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `sprint_planning` | `GS-SPL-04` · task_contracts | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `sprint_planning` | `GS-SPL-05` · dependency_design | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `sprint_planning` | `GS-SPL-06` · execution_planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `sprint_planning` | `GS-SPL-07` · coverage_analysis | `check` | `verification`, `executions`, `artifacts` | `Task.attach_observations + GateAssessment` |
| `sprint_planning` | `GS-SPL-08` · sprint_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `sprint_planning` | `GS-SPL-09` · remediation | `revise` | `tasks`, `content`, `verification`, `executions` | `Task.propose_resolution + Task.apply_submission` |
| `sprint_planning` | `GS-SPL-10` · remediation_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `sprint_planning` | `GS-SPL-11` · publication | `publish` | `tasks`, `sprints`, `workspaces`, `operations` | `Owner.publish through PublicationCoordinator` |
| `task_planning` | `GS-TPL-01` · framing | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `task_planning` | `GS-TPL-02` · classification | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `task_planning` | `GS-TPL-03` · requirements | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `task_planning` | `GS-TPL-04` · acceptance_design | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `task_planning` | `GS-TPL-05` · work_planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `task_planning` | `GS-TPL-06` · verification_planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `task_planning` | `GS-TPL-07` · task_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `task_planning` | `GS-TPL-08` · remediation | `revise` | `tasks`, `content`, `verification`, `executions` | `Task.propose_resolution + Task.apply_submission` |
| `task_planning` | `GS-TPL-09` · remediation_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `task_planning` | `GS-TPL-10` · publication | `publish` | `tasks`, `sprints`, `workspaces`, `operations` | `Owner.publish through PublicationCoordinator` |
| `test_development` | `GS-TST-01` · baseline | `observe` | `executions`, `artifacts`, `verification` | `Task.attach_observations` |
| `test_development` | `GS-TST-02` · coverage_planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `test_development` | `GS-TST-03` · test_planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `test_development` | `GS-TST-04` · test_implementation | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `test_development` | `GS-TST-05` · sensitivity_verification | `check` | `verification`, `executions`, `artifacts` | `Task.attach_observations + GateAssessment` |
| `test_development` | `GS-TST-06` · test_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `test_development` | `GS-TST-07` · remediation | `revise` | `tasks`, `content`, `verification`, `executions` | `Task.propose_resolution + Task.apply_submission` |
| `test_development` | `GS-TST-08` · remediation_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |
| `verification` | `GS-VER-01` · planning | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `verification` | `GS-VER-02` · execution | `check` | `verification`, `executions`, `artifacts` | `Task.attach_observations + GateAssessment` |
| `verification` | `GS-VER-03` · analysis | `produce` | `tasks`, `content`, `verification` | `Task.apply_submission` |
| `verification` | `GS-VER-04` · self_inspection | `inspect` | `tasks`, `content`, `verification` | `Task.record_inspection` |

## Итог

| Handler | Узлов |
|---|---:|
| `apply_plan` | 2 |
| `check` | 9 |
| `inspect` | 24 |
| `observe` | 8 |
| `produce` | 43 |
| `publish` | 3 |
| `revise` | 9 |

Исходные условия feedback остаются в [неизменённой карте](reference/feedback-map.json).
DDD-декомпозиция меняет владельцев кода и доступ к данным, а не бизнес-переходы и не
пользовательский порядок приёмки.
