"""Task-owned Requirements mutations; SQL and applied status belong to the adapter."""
from copy import deepcopy

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
        self.commands = RequirementsCommands(runtime.requirements_store,
            runtime.cfg['batch']['max_items'], writer=self)

    @staticmethod
    def capture_entry(sql):
        snapshot = sql.capture()
        snapshot.close()
        return {'entry': snapshot.proof(), 'entry_sources': sql.sources(),
                'configuration': sql.configuration(), 'originals': {}, 'containers': {}, 'completion': None}

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
        original = {**descriptor}
        if plan is not None:
            original['request_digest'] = plan['request_digest']
        if identity in context['originals'] and context['originals'][identity] != original:
            raise PoiseError('Migration publication intent conflict; preserve the original pair')
        context['originals'][identity] = original
        context['containers'][identity] = {**descriptor, 'contributions': [original]}
        context['completion'] = None
        self._save(context)

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
        migrations = self.sql.migrations()
        dependencies = [migration.id for migration in migrations if migration.id in snapshot.applied]
        planner = self.sql.plan_command if operation == 'command' else self.sql.plan_apply
        try:
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
        self.sql.execute_ids('apply', [identity], expected_revision=snapshot.state['revision'],
                             expected_state=snapshot.state_hash, guards={})
        actual = self.sql.capture()
        actual.close()
        if actual.state_hash != plan['after']:
            raise PoiseError('Actual migration result differs from its prepared literal result; inspect the database before retrying')
        return {key: value for key, value in plan['response'].items() if key != 'status'}

    def _migrations(self, body):
        if body == {'action': 'status'}:
            return self.sql.status()
        if set(body) != {'action', 'migration_ids', 'expected_revision', 'expected_state'}:
            raise PoiseError('Migration action requires explicit action, migration_ids, expected_revision and expected_state')
        identities = body['migration_ids']
        if not isinstance(identities, list) or any(type(value) is not str for value in identities):
            raise PoiseError('Migration IDs must be a list of identities')
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
        for identity in identities:
            if identity not in migrations:
                raise PoiseError('Requested migration is outside the actual Task sources')
            role = self.sql.role(migrations[identity])
            self.runtime.require_requirements_scope(self.task, self.sql, [(role, identity)])
            if identity not in self.context['originals']:
                name = f'{role}/{identity}.sql'
                if name in self.context['entry_sources']:
                    raise PoiseError('A foreign entry migration is not owned by this Task')
                self._publish_context({'id': identity, 'role': role,
                    'forward': sources[name], 'reverse': sources[f'{role}/{identity}.rollback.sql']})
        guards = {identity: value['target_guard'] for identity, value in self.context['originals'].items()
                  if 'target_guard' in value}
        return self.sql.execute_ids(body['action'], identities,
            expected_revision=body['expected_revision'], expected_state=body['expected_state'], guards=guards)

    def export_legacy(self, body):
        identity = self.sql.export_identity(body.get('request_id'))
        self.runtime.require_requirements_scope(self.task, self.sql, [('data', identity)])
        snapshot = self.sql.capture()
        snapshot.close()
        dependencies = [migration.id for migration in self.sql.migrations() if migration.id in snapshot.applied]
        plan = self.sql.export_legacy(body, dependencies, self.context['originals'].get(identity))
        if plan.get('unchanged'):
            return {'status': 'unchanged', 'migration_ids': []}
        if not plan.get('reuse'):
            descriptor = self.sql.publish_pair(identity, plan['forward'], plan['reverse'], role='data')
            descriptor.update({key: plan[key] for key in ('export_request', 'source_state', 'target_guard')})
            self._publish_context(descriptor)
        return {'status': 'generated', 'migration_ids': [identity]}
