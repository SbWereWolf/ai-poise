"""Task-owned Requirements mutations; SQL and applied status belong to the adapter."""
from copy import deepcopy
import hashlib
import json

from ..modules.foundation.errors import PoiseError
from .requirements_registry import RequirementsCommands


class TaskRequirementsMigrations:
    def __init__(self, runtime, task, sql):
        self.runtime = runtime
        self.task = task
        self.sql = sql
        self.context = deepcopy(task.get('requirements_migrations'))
        if self.context is None:
            raise PoiseError('Requirements migration Task entry context is missing; start a new explicit Task execution')
        if self.context['configuration'] != sql.configuration():
            raise PoiseError('Requirements migration configuration changed after Task entry; restore the captured configuration')
        if not isinstance(self.context.get('order'), list):
            raise PoiseError('Task migration order is missing; start a new explicit Task execution')
        self.commands = RequirementsCommands(runtime.requirements_store,
            runtime.cfg['batch']['max_items'], writer=self)

    @staticmethod
    def capture_entry(sql):
        snapshot = sql.capture()
        snapshot.close()
        return {'entry': snapshot.proof(), 'entry_sources': sql.sources(),
                'configuration': sql.configuration(), 'originals': {}, 'containers': {},
                'order': [], 'entry_private': snapshot.private_hash,
                'final_private': snapshot.private_hash, 'completion': None}

    def _save(self, context):
        self.runtime.task_commands.save_requirements_migrations(
            self.task['id'], self.runtime.session, self.context, context,
        )
        self.context = deepcopy(context)

    def _require_sources(self):
        current = self.sql.sources()
        for name, expected in self.context['entry_sources'].items():
            if current.get(name) != expected:
                raise PoiseError('Foreign entry migration content changed; restore its original bytes')
        for identity, container in self.context['containers'].items():
            for suffix, field in (('.sql', 'forward'), ('.rollback.sql', 'reverse')):
                name = f"{container['role']}/{identity}{suffix}"
                if current.get(name) != container[field]:
                    raise PoiseError('Published migration bytes changed or are missing; restore the delivered pair')

    def _publish_context(self, descriptor, *, plan=None):
        context = deepcopy(self.context)
        identity = descriptor['id']
        if identity not in context['containers'] and (
            f"{descriptor['role']}/{identity}.sql" in context['entry_sources']
            or identity in context['entry']['applied']
        ):
            raise PoiseError('A foreign entry migration is not owned by this Task')
        original = {**descriptor}
        if plan is not None:
            original['request_digest'] = plan['request_digest']
        if identity in context['originals'] and context['originals'][identity] != original:
            raise PoiseError('Migration publication intent conflict; preserve the original pair')
        context['originals'][identity] = original
        context['containers'][identity] = {**descriptor, 'contributions': [original]}
        context['completion'] = None
        self._save(context)

    def _remember_order(self, action, identities):
        affected = self.sql.ordered_ids(action, identities)
        context = deepcopy(self.context)
        if action == 'apply':
            context['order'] = [identity for identity in context['order'] if identity not in affected] + affected
        context['completion'] = None
        self._save(context)
        return affected

    def _set_final(self, actual):
        context = deepcopy(self.context)
        context['final_private'] = actual.private_hash
        self._save(context)

    def _admit_execution(self, identity, direction):
        # Called after the captured target guard, immediately before stock SQL.
        # Admission survives failure: an uncertain body is never a mere export.
        context = deepcopy(self.context)
        if direction == 'before':
            context['order'] = [item for item in context['order'] if item != identity] + [identity]
        elif identity not in context['order']:
            context['order'].append(identity)
        context['completion'] = None
        self._save(context)

    def _export_deliverables(self):
        return {identity: container for identity, container in self.context['containers'].items()
                if 'export_request' in container and identity not in self.context['order']}

    def execute(self, request):
        if request == {'operation': 'migrations', 'input': {'action': 'status'}}:
            self.runtime.require_requirements_scope(self.task, self.sql, [], writing=False)
            self._require_sources()
            return self.sql.status()
        self.runtime.require_requirements_scope(self.task, self.sql, [])
        self.sql.require_untracked()
        with self.sql.locked():
            self._require_sources()
            operation, body = request['operation'], request['input']
            if operation in ('command', 'apply'):
                return {'status': 'applied', **getattr(self.commands, operation)(body)}
            if operation == 'import_bootstrap':
                if set(body) != {'path', 'request_id'}:
                    raise PoiseError('Requirements import requires path and request_id')
                return {'status': 'imported', **self.commands.import_bootstrap(body['path'], body['request_id'])}
            if operation == 'migrations':
                return self._migrations(body)
            if operation == 'export':
                return self.export_legacy(body)
            if operation == 'migrate':
                return self.migrate(body)
            raise PoiseError(f'Unknown owned Requirements operation: {operation}')

    def command(self, request):
        return self._mutate('command', request)

    def apply(self, request):
        return self._mutate('apply', request)

    def _mutate(self, operation, request):
        snapshot = self.sql.capture()
        try:
            migrations = self.sql.migrations()
            dependencies = [migration.id for migration in migrations if migration.id in snapshot.applied]
            planner = self.sql.plan_command if operation == 'command' else self.sql.plan_apply
            plan = planner(snapshot, request, self.runtime.cfg['batch']['max_items'], dependencies)
        finally:
            snapshot.close()
        identity = plan['id']
        self.runtime.require_requirements_scope(self.task, self.sql, [('data', identity)])
        if plan['replay']:
            if identity in self.context['originals']:
                containers = [key for key, value in self.context['containers'].items()
                              if any(item['id'] == identity for item in value['contributions'])]
                if not containers or any(key not in snapshot.applied for key in containers):
                    raise PoiseError('Migration body/status outcome is inconsistent; inspect and repair yoyo status manually')
            return {key: value for key, value in plan['response'].items() if key != 'status'}
        if identity in snapshot.applied:
            raise PoiseError('Migration applied status has no matching business receipt; repair yoyo status manually')
        descriptor = self.sql.publish_pair(identity, plan['forward'], plan['reverse'], role='data')
        self._publish_context(descriptor, plan=plan)
        self._remember_order('apply', [identity])
        self.sql.execute_ids('apply', [identity], expected_revision=snapshot.state['revision'],
                             expected_state=snapshot.state_hash, guards={})
        actual = self.sql.capture()
        actual.close()
        if actual.state_hash != plan['after']:
            raise PoiseError('Actual migration result differs from its prepared literal result; inspect the database before retrying')
        self._set_final(actual)
        return {key: value for key, value in plan['response'].items() if key != 'status'}

    def _migrations(self, body):
        if body == {'action': 'status'}:
            return self.sql.status()
        if set(body) != {'action', 'migration_ids', 'expected_revision', 'expected_state'}:
            raise PoiseError('Migration action requires explicit action, migration_ids, expected_revision and expected_state')
        identities = body['migration_ids']
        self.sql.validate_action(body['action'], identities, body['expected_revision'], body['expected_state'])
        migrations = {migration.id: migration for migration in self.sql.migrations()}
        sources = self.sql.sources()
        snapshot = self.sql.capture()
        snapshot.close()
        receipt_digests = {row[1] for row in snapshot.state['receipts']}
        for identity in identities:
            container = self.context['containers'].get(identity)
            if container is not None and identity in snapshot.applied and any(
                item.get('request_digest') is not None and item['request_digest'] not in receipt_digests
                for item in container['contributions']
            ):
                raise PoiseError('Migration inverse is committed but its applied status remains; repair yoyo status manually')
        if snapshot.state['revision'] != body['expected_revision'] or snapshot.state_hash != body['expected_state']:
            raise PoiseError('Requirements revision/state changed; read fresh migration status before retrying')
        for identity in identities:
            if identity not in migrations:
                raise PoiseError('Requested migration is outside the actual Task sources')
            role = self.sql.role(migrations[identity])
            self.runtime.require_requirements_scope(self.task, self.sql, [(role, identity)])
            if identity not in self.context['containers']:
                name = f'{role}/{identity}.sql'
                if name in self.context['entry_sources'] or identity in snapshot.applied:
                    raise PoiseError('A foreign entry migration is not owned by this Task')
                self._publish_context({'id': identity, 'role': role,
                    'forward': sources[name], 'reverse': sources[f'{role}/{identity}.rollback.sql']})
        guards = {identity: value['target_guard'] for identity, value in self.context['containers'].items()
                  if 'target_guard' in value}
        result = self.sql.execute_ids(body['action'], identities,
            expected_revision=body['expected_revision'], expected_state=body['expected_state'],
            guards=guards, admission=self._admit_execution)
        actual = self.sql.capture()
        actual.close()
        self._set_final(actual)
        return result

    def migrate(self, body):
        if body != {}:
            raise PoiseError('Historical Requirements migration takes an empty input object')
        snapshot = self.sql.capture()
        try:
            plan = self.sql.historical_plan(snapshot)
        finally:
            snapshot.close()
        if plan is None:
            return {'status': 'migrated', 'from_version': 2, 'to_version': 2, 'changed': False}
        identity = self.sql.request_identity('schema-1-to-2:' + self.task['id'])
        self.runtime.require_requirements_scope(self.task, self.sql, [('schema', identity)])
        descriptor = self.sql.publish_pair(identity, plan['forward'], plan['reverse'], role='schema')
        self._publish_context(descriptor)
        self._remember_order('apply', [identity])
        self.sql.execute_ids('apply', [identity], expected_revision=snapshot.state['revision'],
                             expected_state=snapshot.state_hash, guards={})
        actual = self.sql.capture()
        actual.close()
        self._set_final(actual)
        return {'status': 'migrated', 'from_version': 1, 'to_version': 2, 'changed': True}

    def export_legacy(self, body):
        identity = self.sql.export_identity(body.get('request_id'))
        self.runtime.require_requirements_scope(self.task, self.sql, [('data', identity)])
        plan = self.sql.export_legacy(body, self.context['originals'].get(identity))
        if plan.get('unchanged'):
            return {'status': 'unchanged', 'migration_ids': []}
        if not plan.get('reuse'):
            descriptor = self.sql.publish_pair(identity, plan['forward'], plan['reverse'], role='data')
            descriptor.update({key: plan[key] for key in ('export_request', 'source_state', 'target_guard')})
            self._publish_context(descriptor)
        return {'status': 'generated', 'migration_ids': [identity]}

    @staticmethod
    def _fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(',', ':')).encode('utf-8')).hexdigest()

    def _intent(self):
        return self._fingerprint({key: self.context[key] for key in (
            'entry', 'entry_private', 'final_private', 'entry_sources', 'configuration',
            'originals', 'containers', 'order')})

    def _expected_sources(self):
        expected = dict(self.context['entry_sources'])
        for identity, container in self.context['containers'].items():
            for suffix, field in (('.sql', 'forward'), ('.rollback.sql', 'reverse')):
                expected[f"{container['role']}/{identity}{suffix}"] = container[field]
        return expected

    def require_completion(self):
        self._require_sources()
        if not self.context['containers']:
            if self.context['originals'] or self.context['order'] or self.sql.sources() != self.context['entry_sources']:
                raise PoiseError('Незарегистрированные SQL-изменения; примените собственные полные пары до завершения Task')
            return
        proof = self.context['completion']
        if (not isinstance(proof, dict) or proof.get('phase') != 'complete'
                or proof.get('kind') != ('migration_cycle' if self.context['order'] else 'export_artifacts')
                or proof.get('intent') != self._intent() or proof.get('sources') != self.sql.sources()):
            raise PoiseError('Цикл миграций Task не подтверждён; выполните writable verify после проверки исходников и штатного состояния yoyo')
        snapshot = self.sql.capture()
        snapshot.close()
        if proof['state'] != self._fingerprint(snapshot.proof()):
            raise PoiseError('БД или история yoyo изменились после проверки; восстановите подтверждённое состояние либо выполните явный rework')

    def plan_completion(self):
        self.runtime.require_requirements_scope(self.task, self.sql, [])
        self.sql.require_untracked()
        self._require_sources()
        if not self.context['containers']:
            self.require_completion()
            return None
        if self.context['completion'] is not None:
            self.require_completion()
            return None
        if self.sql.sources() != self._expected_sources():
            raise PoiseError('Есть незарегистрированные SQL-файлы; примените либо явно уберите собственные неподанные пары до завершения Task')
        snapshot = self.sql.capture()
        snapshot.close()
        deliverables = self._export_deliverables()
        mutations = {identity: container for identity, container in self.context['containers'].items()
                     if identity not in deliverables}
        if (snapshot.private_hash != self.context['final_private']
                or set(snapshot.applied) != set(self.context['entry']['applied']) | set(mutations)):
            raise PoiseError('Состояние БД или applied-история не соответствует Task; проверьте yoyo list и вручную восстановите известное состояние')
        plan = self.sql.completion_plan({**self.context, 'containers': mutations})
        self.runtime.require_requirements_scope(self.task, self.sql,
            [(group['role'], group['id']) for group in plan])
        return plan

    def _boundary(self, private_hash, identities):
        snapshot = self.sql.capture()
        snapshot.close()
        if (snapshot.private_hash != private_hash or snapshot.applied != sorted(identities)
                or any(snapshot.records.get(identity) != record
                       for identity, record in self.context['entry']['records'].items())):
            raise PoiseError('Зафиксированное состояние после миграций отличается от ожидаемого; остановитесь и восстановите БД/историю штатными средствами')
        return snapshot

    def _phase(self, phase, checkpoint):
        context = deepcopy(self.context)
        context['completion'] = {'phase': phase, 'checkpoint': checkpoint}
        self._save(context)

    def _complete(self, checkpoint, snapshot, kind):
        context = deepcopy(self.context)
        context['completion'] = {'phase': 'complete', 'kind': kind, 'checkpoint': checkpoint,
            'sources': self.sql.sources(), 'intent': self._intent(),
            'state': self._fingerprint(snapshot.proof())}
        self._save(context)
        self.require_completion()

    def _execute_phase(self, action, identities, checkpoint, phase, observations=None):
        self._phase(phase, checkpoint)
        before = self.sql.capture()
        before.close()
        guards = {identity: container['target_guard'] for identity, container in self.context['containers'].items()
                  if 'target_guard' in container}
        result = self.sql.execute_ids(action, identities, expected_revision=before.state['revision'],
            expected_state=before.state_hash, guards=guards, observations=observations)
        expected = list(reversed(identities)) if action == 'rollback' else identities
        if result['migration_ids'] != expected:
            raise PoiseError('Штатный порядок yoyo отличается от записанного; сохраните исходники и проверьте граф зависимостей')

    def prepare_completion(self, plan, checkpoint):
        deliverables = deepcopy(self._export_deliverables())
        original = {identity: deepcopy(container) for identity, container in self.context['containers'].items()
                    if identity not in deliverables}
        original_ids = list(self.context['order'])
        entry_ids = set(self.context['entry']['applied'])
        final_ids = [group['id'] for group in plan]
        final_private = self.context['final_private']
        try:
            if not original:
                final = self._boundary(self.context['entry_private'], entry_ids)
                self._complete(checkpoint, final, 'export_artifacts')
                return
            self._execute_phase('rollback', original_ids, checkpoint, 'original_down')
            self._boundary(self.context['entry_private'], entry_ids)
            self._require_sources()
            self._phase('publish_final', checkpoint)
            delivered = self.sql.publish_completion(plan, original)
            context = deepcopy(self.context)
            context['containers'] = {**deliverables, **delivered}
            context['order'] = final_ids
            self._save(context)
            self.sql.remove_superseded(original, delivered)
            self._require_sources()
            if self.sql.sources() != self._expected_sources():
                raise PoiseError('Итоговый набор SQL не соответствует Task; восстановите подтверждённые пары')
            observations = []
            self._execute_phase('apply', final_ids, checkpoint, 'final_up', observations)
            self._boundary(final_private, entry_ids | set(final_ids))
            if [item['id'] for item in observations] != final_ids:
                raise PoiseError('Не все обычные SQL-тела подтверждены; проверьте фактическую историю yoyo')
            context = deepcopy(self.context)
            for item in observations:
                context['containers'][item['id']]['target_guard'] = {
                    key: value for key, value in item.items() if key != 'id'}
            self._save(context)
            self._execute_phase('rollback', final_ids, checkpoint, 'final_down')
            self._boundary(self.context['entry_private'], entry_ids)
            self._execute_phase('apply', final_ids, checkpoint, 'last_up')
            final = self._boundary(final_private, entry_ids | set(final_ids))
            self._complete(checkpoint, final, 'migration_cycle')
        except Exception as exc:
            phase = (self.context.get('completion') or {}).get('phase', 'before_cycle')
            raise PoiseError(
                f'Цикл миграций остановлен на {phase}: {exc}. Task не завершена. '
                f'Часть SQL могла зафиксироваться без applied-записи; проверьте yoyo list для выбранной БД, '
                f'вручную исправьте состояние yoyo/SQL, используя git show {checkpoint}:<SQL-path>. '
                'После восстановления выполните явный owned apply/rollback со свежими guards и новый writable verify. '
                'Автоматическое восстановление не выполнялось.'
            ) from exc
