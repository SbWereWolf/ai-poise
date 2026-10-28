from .actions import SqliteActionRepository
from .sprints import SqliteSprintRepository
from .evidence import SqliteEvidenceRepository
from .tasks import SqliteTaskRepository, SqliteExecutionRepository
from .ownership import SqliteOwnershipRepository
from .accounting import SqliteAccountingCycles
from .work_packets import SqliteWorkPacketRepository
from .artifacts import SqliteArtifactRepository
from .task_delivery import SqliteTaskDeliveryRepository
from .integration_scopes import SqliteIntegrationScopeRepository
from .worktree_preparation import SqlitePreparationRepository
from .stage_result_handoff import SqliteHandoffRepository


class SqliteUnitOfWork:
    """Commit on successful context exit; rollback on any exception."""
    def __init__(self, database, workflows):
        self.database = database
        self.workflows = workflows

    def capture(self, read_set):
        from .task_inputs import capture_inputs
        with self.database.preparation_transaction() as connection:
            inputs = capture_inputs(connection, read_set)
        return inputs

    def capture_sprint_view(self, sprint_id, actor, view):
        from .sprint_snapshot import capture_sprint_view
        with self.database.preparation_transaction() as connection:
            snapshot = capture_sprint_view(connection, sprint_id, actor, view)
        return snapshot

    def capture_sprint(self, sprint_id, actor, task_ids=(), creation_requests=()):
        from .sprint_snapshot import capture_sprint
        with self.database.preparation_transaction() as connection:
            snapshot = capture_sprint(connection, sprint_id, actor, task_ids, creation_requests)
        return snapshot

    def newborn_member_fact(self, snapshot, newborn, metadata):
        old = next((row for row in snapshot.member_rows if row['id'] == newborn.task_id), None)
        return {'id':newborn.task_id, 'status':'newborn', 'claimed_by':newborn.claimed_by,
            'version':newborn.version, 'stage_index':0, 'iteration':1, 'metadata':metadata,
            'execution':None if old is None else old['execution'],
            'worktree_owner':None if old is None else old['worktree_owner']}

    def prepared_member_fact(self, snapshot, write):
        old = next((row for row in snapshot.member_rows if row['id'] == write.state.task_id), None)
        state = write.state
        return {'id':state.task_id, 'status':state.status.value, 'claimed_by':state.claimed_by,
            'version':state.version, 'stage_index':state.stage_index, 'iteration':state.iteration,
            'metadata':write.metadata if write.metadata is not None else old['metadata'],
            'execution':None if old is None else old['execution'],
            'worktree_owner':None if old is None else old['worktree_owner']}

    def member_fact_with_execution(self, fact, execution):
        return {**fact, 'execution':execution}

    def creation_member_fact(self, write, snapshot):
        if write.rows is not None:
            return self.prepared_member_fact(snapshot, write.rows)
        row = write.receipt.task_fact
        return (dict(row) if write.requirements_patch is None else
                {**row, 'metadata':write.requirements_patch.after})

    def promotion_member_fact(self, snapshot, candidate):
        fact = self.prepared_member_fact(snapshot, candidate['write'])
        return {**fact, 'worktree_owner':None} if candidate['release_worktree'] else fact

    def prepare_membership(self, inputs, task_id, expected_sprint, target_sprint, expected_version):
        from .task_writes import prepare_membership
        return prepare_membership(inputs.tasks[task_id].header, expected_sprint, target_sprint, expected_version)

    def prepare_membership_fact(self, fact, expected_sprint, target_sprint, expected_version):
        from .task_writes import prepare_membership
        return prepare_membership(fact, expected_sprint, target_sprint, expected_version)

    def membership_member_fact(self, snapshot, prepared):
        old = next(row for row in snapshot.member_rows if row['id'] == prepared.task_id)
        return {**old, 'version':prepared.expected_version + 1, 'metadata':prepared.metadata}

    def require_sprint(self, snapshot):
        from .sprint_snapshot import require_sprint
        require_sprint(self.tasks.db, snapshot)

    def require_inputs(self, inputs):
        from .task_inputs import require_inputs
        require_inputs(self.tasks.db, inputs)

    def prepare_change(self, inputs, change):
        from .task_writes import prepare_task_write
        raw = inputs.tasks[change.task.state.task_id]
        return prepare_task_write(raw, change, raw.header['version'])

    def restore_progression_request(self, raw, request_id, digest):
        return SqliteTaskRepository.restore_progression_request(raw, request_id, digest)

    def prepare_progression_entry(self, kind, data):
        return SqliteTaskRepository.prepare_progression_entry(kind, data)

    def restore_stage_contract_receipt(self, raw, task_id, request_id, intent):
        return SqliteTaskRepository.restore_stage_contract_receipt(raw, task_id, request_id, intent)

    def prepare_stage_contract_change(self, inputs, change, audit):
        from .task_writes import prepare_stage_contract_change
        raw = inputs.tasks[change.task.state.task_id]
        return prepare_stage_contract_change(raw, change, raw.header['version'], audit)

    def prepare_restart_event(self, reason, authorization):
        return SqliteTaskRepository.prepare_restart_event(reason, authorization)

    def prepare_result(self, report):
        return SqliteTaskRepository.prepare_result(report)

    def prepare_ownership_reconciliation(self, proof):
        from .ownership import encode
        return encode(proof)

    def prepare_cycle_release(self, inputs, actor, task_id):
        return SqliteAccountingCycles.prepare_release(inputs.cycles[(actor, task_id)])

    def prepare_release_receipt(self, intent, actor, result):
        return SqliteOwnershipRepository.prepare_ordinary_release(intent, actor, result)

    def timeout_history(self, profile):
        with self.database.preparation_transaction() as connection:
            rows = SqliteEvidenceRepository.capture_timeout_history(connection)
        return SqliteEvidenceRepository.restore_timeout_history(rows, profile)

    def prepare_evidence(self, receipt):
        return SqliteEvidenceRepository.prepare(receipt)

    def prepare_execution(self, inputs, task_id, data):
        previous = inputs.execution(task_id)
        if previous is None:
            return SqliteExecutionRepository.prepare_create(data)
        return SqliteExecutionRepository.prepare_save(previous[0], data)

    def prepare_claim_changes(self, inputs, changes):
        from dataclasses import replace
        from .task_writes import prepare_task_write
        from .tasks import encode
        raw = inputs.tasks[changes[0].task.state.task_id]
        result = []
        for change in changes:
            if change.task.state.task_id != raw.header['id'] or change.submission is not None:
                from ...modules.foundation.errors import PoiseError
                raise PoiseError('Claim preparation requires one Task and no new submission')
            prepared = prepare_task_write(raw, change, raw.header['version'])
            result.append(prepared)
            task = change.task
            raw = replace(raw, header={**raw.header, 'version': task.state.version,
                'status': task.state.status.value, 'stage_index': task.state.stage_index,
                'iteration': task.state.iteration, 'claimed_by': task.state.claimed_by,
                'metadata': prepared.metadata or raw.header['metadata']},
                workflow=prepared.workflow or raw.workflow,
                proof=prepared.proof or raw.proof,
                content=encode(task.content_policy.to_layers()),
                methods=tuple(encode(entry.to_dict()) for entry in task.check_registry.entries))
        return tuple(result)

    def prepare_newborn(self, inputs, newborn, config_hash):
        import json
        from .tasks import encode
        previous = json.loads(inputs.tasks[newborn.task_id].header['metadata'])
        return encode(SqliteTaskRepository.newborn_metadata(newborn, config_hash, previous))

    def prepare_duplicate_newborn(self, newborn, config_hash, parent_id):
        return SqliteTaskRepository.prepare_duplicate_link(self.prepare_newborn_create(newborn, config_hash), parent_id)

    def prepare_newborn_create(self, newborn, config_hash):
        from .tasks import encode
        return encode(newborn.metadata(config_hash))

    def prepare_action_receipt(self, request_id, digest, result):
        from .tasks import encode
        return encode({'request_id': request_id, 'digest': digest, 'result': result})

    def prepare_sprint_receipt(self, result):
        from .tasks import encode
        return encode(result)

    def restore_sprint_receipt(self, raw):
        import json
        return json.loads(raw)

    def prepare_sprint(self, record):
        return SqliteSprintRepository.prepare(record)

    def prepare_task_creation(self, task, metadata):
        from .task_writes import prepare_task_creation
        return prepare_task_creation(task, metadata, 'created')

    def new_promotion_source(self, newborn, config_hash):
        from .task_writes import TaskPromotionSource
        return TaskPromotionSource(newborn.task_id, newborn.version,
                                    self.prepare_newborn_create(newborn, config_hash), ())

    def existing_promotion_source(self, inputs, task_id):
        from .task_writes import TaskPromotionSource
        raw = inputs.tasks[task_id]
        return TaskPromotionSource(task_id, raw.header['version'], raw.header['metadata'], raw.methods)

    def prepare_promotion(self, source, task, metadata):
        from .task_writes import prepare_promotion
        return prepare_promotion(source, task, metadata)

    def prepare_requirements_context(self, inputs, task_id, snapshot, agreement):
        return SqliteTaskRepository.prepare_requirements_context(task_id,
            inputs.tasks[task_id].header['metadata'], snapshot, agreement)

    def prepare_requirements_patch(self, receipt, context):
        import json
        from .tasks import encode
        from .task_writes import PreparedRequirementsPatch
        if receipt.requirements_context() == context:
            return None
        metadata = json.loads(receipt.metadata)
        metadata['requirements_snapshot'] = context['requirements_snapshot']
        metadata['requirements_agreement'] = context['requirements_agreement']
        return PreparedRequirementsPatch(receipt.task_id, receipt.metadata, encode(metadata))

    def prepare_action(self, inputs, task_id, stage, iteration, value):
        return SqliteActionRepository.prepare(inputs.action_binding(task_id, stage, iteration), value)

    def __enter__(self):
        self._transaction = self.database.transaction()
        connection = self._transaction.__enter__()
        self.sprints = SqliteSprintRepository(connection)
        self.tasks = SqliteTaskRepository(connection)
        self.actions = SqliteActionRepository(connection,self.tasks)
        self.execution = SqliteExecutionRepository(connection)
        self.evidence = SqliteEvidenceRepository(connection)
        self.ownership = SqliteOwnershipRepository(connection,self.workflows)
        self.accounting_cycles = SqliteAccountingCycles(connection)
        self.work_packets = SqliteWorkPacketRepository(connection)
        self.artifacts = SqliteArtifactRepository(connection)
        self.task_delivery = SqliteTaskDeliveryRepository(connection)
        self.integration_scopes = SqliteIntegrationScopeRepository(connection)
        self.preparation = SqlitePreparationRepository(connection)
        self.handoffs = SqliteHandoffRepository(connection)
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._transaction.__exit__(exc_type, exc, traceback)
