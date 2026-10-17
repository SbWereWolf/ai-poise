"""Application graph rules, independent of SQL and transaction mechanics."""
from copy import deepcopy

from poise.modules.foundation.errors import DomainError
from poise.modules.requirements_registry.domain import RequirementsRegistry
from .outcomes import OutcomeLedger


def _exact(value, fields, where):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise DomainError(f'{where}: required exact fields {sorted(fields)}')


def _identifier(value, where):
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f'{where}: required nonempty identifier')
    return value


def _positive_integer(value, where):
    if type(value) is not int or value <= 0:
        raise DomainError(f'{where}: required positive integer')
    return value


class RequirementsGraph:
    """Both graph edges and their applicable current statement projection."""

    query_kinds = (
        'task_chain', 'application_tasks', 'get_task', 'task_history',
        'history_requirement', 'snapshot', 'search_requirements',
        'task_basis', 'task_creation', 'current_satisfaction', 'outcome_state',
    )

    @classmethod
    def restore(cls, requirements, links, task_identities,
                task_requirement_versions, application_task_links, revisions,
                owner_revision, outcomes, decisions):
        graph = cls()
        graph._registry = RequirementsRegistry.restore(requirements, links)
        graph._current = {
            row['task_id']: row['current_statement_revision'] for row in task_identities
        }
        parents = {}
        for row in application_task_links:
            identity = (row['task_id'], row['statement_revision'], row['key'])
            parents.setdefault(identity, []).append(row['application_id'])
        graph._entries = {}
        for row in task_requirement_versions:
            identity = (row['task_id'], row['statement_revision'])
            graph._entries.setdefault(identity, []).append({
                'key': row['key'], 'status': row['status'], 'text': row['text'],
                'applications': sorted(parents.get((*identity, row['key']), [])),
            })
        graph._revisions = revisions
        graph._new_statements = {}
        graph._owner_revision = owner_revision
        graph._publications = {}
        graph._outcomes = OutcomeLedger.restore(outcomes, decisions)
        return graph

    def _task(self, task_id):
        _identifier(task_id, 'task_id')
        if task_id not in self._current:
            raise DomainError(f'unknown Task: {task_id}')
        return self._current[task_id]

    def _statement(self, task_id, revision):
        self._task(task_id)
        _positive_integer(revision, 'statement revision')
        identity = (task_id, revision)
        if identity in self._new_statements:
            return deepcopy(self._new_statements[identity])
        return self._revisions.statement(task_id, revision)

    def _application(self, identifier):
        value = self._registry.requirement(identifier)
        if value['level'] != 'application':
            raise DomainError(f'unknown Application requirement: {identifier}')
        return value

    def validate_statement(self, statement, max_items):
        _exact(statement, {'goal', 'task_requirements', 'definition_of_done'}, 'Task statement')
        if not isinstance(statement['goal'], str) or not statement['goal'].strip():
            raise DomainError('Task goal must be nonempty text')
        dod = statement['definition_of_done']
        if not isinstance(dod, list) or any(not isinstance(v, str) or not v.strip() for v in dod):
            raise DomainError('Task definition_of_done must contain nonempty text')
        entries = statement['task_requirements']
        if not isinstance(entries, list) or len(entries) > max_items:
            raise DomainError('Task requirements must be a bounded list')
        keys = set()
        for entry in entries:
            _exact(entry, {'key', 'status', 'text', 'applications'}, 'Task requirement')
            key = _identifier(entry['key'], 'Task requirement key')
            if key in keys:
                raise DomainError('Task requirement key must be unique')
            keys.add(key)
            if entry['status'] not in ('current', 'future', 'obsolete'):
                raise DomainError('Task requirement status must be current, future or obsolete')
            text = entry['text']
            if not isinstance(text, str) or (not text.strip() and not (
                    entry['status'] == 'future' and text == '')):
                raise DomainError('Task requirement text must be nonempty except a future placeholder')
            applications = entry['applications']
            if not isinstance(applications, list) or len(applications) > max_items:
                raise DomainError('applications must be a bounded list of unique identifiers')
            seen = set()
            for identifier in applications:
                _identifier(identifier, 'applications')
                if identifier in seen:
                    raise DomainError('applications must be unique')
                seen.add(identifier)
                self._application(identifier)
        return deepcopy(statement)

    def _revise_task(self, operation, max_items):
        _exact(operation, {'kind', 'task_id', 'expected_statement_revision', 'statement'},
               'revise_task')
        task_id = operation['task_id']
        current = self._task(task_id)
        expected = _positive_integer(operation['expected_statement_revision'], 'statement revision')
        if expected != current:
            raise DomainError('Task statement revision changed')
        statement = self.validate_statement(operation['statement'], max_items)
        revision = current + 1
        saved = deepcopy(statement)
        self._new_statements[(task_id, revision)] = saved
        self._entries[(task_id, revision)] = deepcopy(saved['task_requirements'])
        self._current[task_id] = revision

    def create_draft(self, task_id, statement, max_items):
        _identifier(task_id, 'Task ID')
        if task_id in self._current:
            raise DomainError(f'Task ID already exists: {task_id}')
        saved = self.validate_statement(statement, max_items)
        self._current[task_id] = 1
        self._new_statements[(task_id, 1)] = saved
        self._entries[(task_id, 1)] = deepcopy(saved['task_requirements'])
        return deepcopy(saved)

    def _unlink(self, operation):
        _exact(operation, {'kind', 'system', 'application'}, 'unlink')
        system = self._registry.requirement(operation['system'])
        if system['level'] != 'system':
            raise DomainError(f'unknown System requirement: {operation["system"]}')
        self._application(operation['application'])
        state = self._registry.to_dict()
        removed = {'system': operation['system'], 'application': operation['application']}
        self._registry = RequirementsRegistry.restore(
            list(state['requirements'].values()),
            [link for link in state['links'] if link != removed],
        )

    def apply(self, operations, max_items):
        if not isinstance(operations, list) or not 0 < len(operations) <= max_items:
            raise DomainError('Requirements batch must be bounded and nonempty')
        graph = deepcopy(self)
        for operation in operations:
            if not isinstance(operation, dict):
                raise DomainError('Requirements operation must be an object')
            kind = operation.get('kind')
            if kind == 'revise_task':
                graph._revise_task(operation, max_items)
            elif kind == 'unlink':
                graph._unlink(operation)
            elif kind == 'publish_task':
                graph._publish_task(operation)
            elif kind == 'record_outcome':
                graph._record_outcome(operation, max_items)
            elif kind == 'select_confirmation':
                graph._select_confirmation(operation)
            else:
                graph._registry = graph._registry.apply([operation], max_items)
        return graph

    @staticmethod
    def _applicable(node, mode):
        return bool(node['text'].strip()) and (
            node['status'] == 'current' if mode == 'current' else node['status'] != 'obsolete'
        )

    def coverage(self, mode=None):
        if mode not in (None, 'current', 'analysis'):
            raise DomainError('coverage mode must be current or analysis')
        state = self._registry.to_dict()
        included = {key: node for key, node in state['requirements'].items()
                    if self._applicable(node, mode)}
        systems = {key for key, node in included.items() if node['level'] == 'system'}
        applications = set(included) - systems
        edges = [edge for edge in state['links']
                 if edge['system'] in systems and edge['application'] in applications]
        task_parents = set()
        for task_id, revision in self._current.items():
            for entry in self._entries.get((task_id, revision), []):
                if self._applicable(entry, mode):
                    task_parents.update(entry['applications'])
        return {
            'application_without_system': sorted(applications - {e['application'] for e in edges}),
            'system_without_application': sorted(systems - {e['system'] for e in edges}),
            'application_without_task': sorted(applications - task_parents),
        }

    def gaps(self):
        requirements = self._registry.to_dict()['requirements']
        result = [
            {'kind': kind, 'requirement_id': identifier, 'text': requirements[identifier]['text']}
            for kind, identifiers in self.coverage().items() for identifier in identifiers
        ]
        result.extend(
            {'kind': 'projected_placeholder', 'level': node['level'],
             'requirement_id': identifier, 'text': ''}
            for identifier, node in requirements.items()
            if node['status'] == 'future' and node['text'] == ''
        )
        for task_id, revision in sorted(self._current.items()):
            for entry in self._entries.get((task_id, revision), []):
                if entry['status'] == 'future' and entry['text'] == '':
                    result.append({'kind': 'projected_placeholder', 'level': 'task',
                                   'task_id': task_id, 'key': entry['key'],
                                   'statement_revision': revision, 'text': ''})
        return result

    def task_chain(self, task_id, key, statement_revision):
        self._task(task_id)
        _identifier(key, 'key')
        _positive_integer(statement_revision, 'statement revision')
        identity = (task_id, statement_revision)
        if (identity not in self._new_statements
                and not self._revisions.has_statement(task_id, statement_revision)):
            raise DomainError(f'unknown Task statement revision: {statement_revision}')
        entry = next((v for v in self._entries.get(identity, []) if v['key'] == key), None)
        if entry is None:
            raise DomainError(f'unknown Task requirement key: {key}')
        applications = [self._application(identifier) for identifier in sorted(entry['applications'])]
        return {
            'task': {'task_id': task_id, 'statement_revision': statement_revision, **deepcopy(entry)},
            'applications': [value for value in applications if value['status'] != 'obsolete'],
        }

    def application_tasks(self, application_id):
        application = self._application(application_id)
        tasks = []
        for task_id, revision in sorted(self._current.items()):
            for entry in sorted(self._entries.get((task_id, revision), []), key=lambda v: v['key']):
                if application_id in entry['applications'] and entry['status'] != 'obsolete':
                    tasks.append({'task_id': task_id, 'statement_revision': revision, **deepcopy(entry)})
        return {'application': application, 'tasks': tasks}

    def get_task(self, task_id):
        revision = self._task(task_id)
        statement = self._statement(task_id, revision)
        return {
            'task_id': task_id, 'statement_revision': revision,
            'snapshot_revision': self._latest_snapshot_revision(task_id),
            'goal': statement['goal'], 'definition_of_done': statement['definition_of_done'],
            'task_requirements': deepcopy(self._entries.get((task_id, revision), [])),
        }

    def _latest_snapshot_revision(self, task_id):
        saved = self._revisions.latest_snapshot_revision(task_id)
        pending = [revision for identifier, revision in self._publications if identifier == task_id]
        return max(([saved] if saved is not None else []) + pending, default=None)

    def _snapshot(self, task_id, snapshot_revision):
        self._task(task_id)
        _positive_integer(snapshot_revision, 'snapshot revision')
        pending = self._publications.get((task_id, snapshot_revision))
        if pending is not None:
            return deepcopy(pending)
        return self._revisions.snapshot(task_id, snapshot_revision)

    def _current_snapshot(self, task_id):
        statement_revision = self._task(task_id)
        for (identifier, revision), snapshot in self._publications.items():
            if identifier == task_id and snapshot['statement_revision'] == statement_revision:
                return revision
        return self._revisions.snapshot_for_statement(task_id, statement_revision)

    def _publish_task(self, operation):
        _exact(operation, {'kind', 'task_id', 'statement_revision'}, 'publish_task')
        task_id = operation['task_id']
        current = self._task(task_id)
        if _positive_integer(operation['statement_revision'], 'statement revision') != current:
            raise DomainError('publication requires the current Task statement')
        if self._current_snapshot(task_id) is not None:
            return
        if not self.task_basis(task_id)['basis_ready']:
            raise DomainError('Task publication requires its complete requirement basis')
        entries = deepcopy(self._entries[(task_id, current)])
        planned = self._registry.plan_task([
            {field: entry[field] for field in ('text', 'status', 'applications')}
            for entry in entries if entry['status'] != 'obsolete'
        ])['snapshot']
        revision = (self._latest_snapshot_revision(task_id) or 0) + 1
        statement = {'task_id': task_id, 'statement_revision': current,
                     **self._statement(task_id, current), 'task_requirements': entries}
        lower = [{'application': application, 'key': entry['key'], 'task_id': task_id}
                 for entry in entries if entry['status'] != 'obsolete'
                 for application in entry['applications']]
        self._publications[(task_id, revision)] = {
            'task_id': task_id, 'statement_revision': current, 'snapshot_revision': revision,
            'registry_revision': self._owner_revision + 1, 'statement': statement,
            'requirements': planned['requirements'],
            'links': {'system_application': planned['links'], 'application_task': sorted(
                lower, key=lambda row: (row['application'], row['key'], row['task_id']))},
        }

    def _record_outcome(self, operation, max_items):
        _exact(operation, {'kind', 'outcome'}, 'record_outcome')
        outcome = operation['outcome']
        if not isinstance(outcome, dict) or not {'task_id', 'snapshot_revision'} <= set(outcome):
            raise DomainError('outcome requires its addressed Task publication')
        snapshot = self._snapshot(outcome['task_id'], outcome['snapshot_revision'])
        self._outcomes.record(outcome, snapshot,
                              self._current_snapshot(outcome['task_id']) == outcome['snapshot_revision'],
                              max_items)

    def _select_confirmation(self, operation):
        _exact(operation, {'kind', 'task_id', 'snapshot_revision', 'key', 'outcome_id'},
               'select_confirmation')
        task_id, revision = operation['task_id'], operation['snapshot_revision']
        snapshot = self._snapshot(task_id, revision)
        if self._current_snapshot(task_id) != revision:
            raise DomainError('confirmation requires the current Task publication')
        key = _identifier(operation['key'], 'Task requirement key')
        if key not in {entry['key'] for entry in snapshot['statement']['task_requirements']}:
            raise DomainError('confirmation key must exist in the addressed snapshot')
        self._outcomes.select(task_id, revision, key, operation['outcome_id'])

    def current_satisfaction(self):
        state = self._registry.to_dict()
        task_rows, children = [], {}
        for task_id, statement_revision in sorted(self._current.items()):
            snapshot_revision = self._current_snapshot(task_id)
            for entry in sorted(self._entries.get((task_id, statement_revision), []),
                                key=lambda value: value['key']):
                applicable = self._applicable(entry, 'current')
                proof = self._outcomes.choice(task_id, snapshot_revision, entry['key'])
                satisfied = applicable and proof is not None
                task_rows.append({
                    'task_id': task_id, 'statement_revision': statement_revision,
                    'snapshot_revision': snapshot_revision, 'key': entry['key'],
                    'applicable': applicable, 'satisfied': satisfied,
                    'evidence_outcome_ids': [proof] if satisfied else [],
                })
                if applicable:
                    for application in entry['applications']:
                        children.setdefault(application, []).append(({
                            'task_id': task_id, 'statement_revision': statement_revision,
                            'key': entry['key'],
                        }, satisfied))
        applications = []
        for identifier, node in sorted(state['requirements'].items()):
            if node['level'] != 'application':
                continue
            applicable = self._applicable(node, 'current')
            related = children.get(identifier, []) if applicable else []
            applications.append({'id': identifier, 'children': [row for row, flag in related],
                                 'applicable': applicable,
                                 'satisfied': bool(related) and all(flag for row, flag in related)})
        systems = []
        for identifier, node in sorted(state['requirements'].items()):
            if node['level'] != 'system':
                continue
            applicable = self._applicable(node, 'current')
            linked = {edge['application'] for edge in state['links'] if edge['system'] == identifier}
            related = [row for row in applications if row['applicable'] and row['id'] in linked]
            if not applicable:
                related = []
            systems.append({'id': identifier, 'children': [row['id'] for row in related],
                            'applicable': applicable,
                            'satisfied': bool(related) and all(row['satisfied'] for row in related)})
        return {'task_requirements': task_rows, 'applications': applications, 'systems': systems}

    def outcome_state(self, task_id, snapshot_revision):
        return self._outcomes.state(self._snapshot(task_id, snapshot_revision))

    def task_basis(self, task_id):
        revision = self._task(task_id)
        requirements = []
        for entry in self._entries.get((task_id, revision), []):
            if entry['status'] == 'obsolete':
                continue
            source = {key: deepcopy(entry[key]) for key in ('text', 'status', 'applications')}
            result = self._registry.plan_task([source])
            requirements.append({
                'key': entry['key'], 'basis_ready': result['status'] == 'ready',
                'gaps': result['gaps'],
            })
        return {
            'task_id': task_id, 'statement_revision': revision,
            'basis_ready': bool(requirements) and all(item['basis_ready'] for item in requirements),
            'requirements': requirements,
        }

    def task_creation(self, task_id):
        self._task(task_id)
        return self._revisions.task_creation(task_id)

    def search_requirements(self, filter, limit, after_id, max_items):
        if not isinstance(filter, dict) or set(filter) - {'level', 'status', 'text_contains'}:
            raise DomainError('search filter requires level, status or text_contains')
        if 'level' in filter and filter['level'] not in ('system', 'application'):
            raise DomainError('search level must be system or application')
        if 'status' in filter and filter['status'] not in ('current', 'future', 'obsolete'):
            raise DomainError('search status must be current, future or obsolete')
        text = filter.get('text_contains', '')
        if not isinstance(text, str):
            raise DomainError('text_contains must be text')
        if type(limit) is not int or not 1 <= limit <= max_items:
            raise DomainError('search limit must be a positive integer within max_items')
        if after_id is not None and (not isinstance(after_id, str) or not after_id):
            raise DomainError('after_id must be a nonempty identifier or null')
        rows = [node for identifier, node in self._registry.to_dict()['requirements'].items()
                if (after_id is None or identifier > after_id)
                and all(node[field] == filter[field] for field in ('level', 'status') if field in filter)
                and text in node['text']]
        page = rows[:limit]
        return {'rows': page, 'next': page[-1]['id'] if len(rows) > limit else None}

    def query(self, query, max_items):
        kind = _identifier(query.get('kind'), 'query kind')
        fields = {
            'task_chain': {'task_id', 'key', 'statement_revision'},
            'application_tasks': {'application_id'}, 'get_task': {'task_id'},
            'task_history': {'task_id'}, 'history_requirement': {'requirement_id'},
            'task_basis': {'task_id'}, 'task_creation': {'task_id'},
            'snapshot': {'task_id', 'snapshot_revision'},
            'outcome_state': {'task_id', 'snapshot_revision'}, 'current_satisfaction': set(),
            'search_requirements': {'filter', 'limit', 'after_id'},
        }
        if kind in fields:
            _exact(query, {'id', 'kind'} | fields[kind], kind)
        if kind == 'task_chain':
            return self.task_chain(query['task_id'], query['key'], query['statement_revision'])
        if kind == 'application_tasks':
            return self.application_tasks(query['application_id'])
        if kind == 'get_task':
            return self.get_task(query['task_id'])
        if kind == 'task_basis':
            return self.task_basis(query['task_id'])
        if kind == 'task_creation':
            return self.task_creation(query['task_id'])
        if kind == 'task_history':
            self._task(query['task_id'])
            return self._revisions.task_history(query['task_id'])
        if kind == 'history_requirement':
            self._registry.requirement(query['requirement_id'])
            return self._revisions.requirement_history(query['requirement_id'])
        if kind == 'snapshot':
            return self._snapshot(query['task_id'], query['snapshot_revision'])
        if kind == 'outcome_state':
            return self.outcome_state(query['task_id'], query['snapshot_revision'])
        if kind == 'current_satisfaction':
            return self.current_satisfaction()
        if kind == 'search_requirements':
            return self.search_requirements(query['filter'], query['limit'], query['after_id'], max_items)
        if kind == 'coverage':
            if set(query) not in ({'id', 'kind'}, {'id', 'kind', 'mode'}):
                raise DomainError('coverage query has unknown fields')
            return self.coverage(query.get('mode'))
        if kind == 'gaps':
            _exact(query, {'id', 'kind'}, 'gaps')
            return self.gaps()
        return self._registry.query(query, max_items)

    def to_dict(self):
        return {
            **self._registry.to_dict(),
            'statements': [{'task_id': task_id, 'statement_revision': revision, **deepcopy(value)}
                           for (task_id, revision), value in sorted(self._new_statements.items())],
            'publications': deepcopy(list(self._publications.values())),
            **self._outcomes.pending(),
        }
