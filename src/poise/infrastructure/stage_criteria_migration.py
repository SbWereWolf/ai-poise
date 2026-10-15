"""Operational composition for the explicit historical stage-criteria cutover.

The frozen transformer is separate from current manifest/editor/Task owners.
SQLite intent and owner receipts describe filesystem effects, not atomicity with them.
"""
from contextlib import closing, ExitStack
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from ..common import configured_root, descendant, load_workflow_migration_config
from ..modules.foundation.errors import PoiseError
from ..modules.goal_config.domain import GoalTypeDefinition
from ..modules.tasks.stage_criteria import StageCriteriaBook
from .catalogue import FileCatalogue
from .goal_config import FileGoalConfigRepository
from .locking import exclusive_lock
from .stage_criteria_history import canonical, identity, workflow_candidate, task_candidate
from .stage_criteria_drafts import draft_candidate
from .task_workflow_migration import _database_snapshot, _read_only_uri


EVENT = 'task_stage_criteria_migration'


def _snapshot(db):
    version, schema, tables = _database_snapshot(db)
    clean = []
    for name, rows in tables:
        if name == 'journal':
            columns = [row[1] for row in db.execute('PRAGMA table_info(journal)')]
            index = columns.index('event')
            rows = tuple(row for row in rows if row[index] != EVENT)
        clean.append((name, rows))
    return identity([version, schema, clean])


def _records(db):
    tasks = [dict(row) for row in db.execute(
        'SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata '
        'FROM tasks ORDER BY id')]
    events = [dict(row) for row in db.execute('SELECT seq,task_id,version,data FROM task_events ORDER BY seq')]
    submissions = [dict(row) for row in db.execute(
        'SELECT seq,task_id,stage,iteration,digest,data FROM submissions ORDER BY seq')]
    return tasks, events, submissions


def _saved(db, request):
    values = []
    for row in db.execute('SELECT data FROM journal WHERE event=? ORDER BY seq', (EVENT,)):
        item = json.loads(row[0])
        if item['request_id'] != request.request_id:
            continue
        if item['request_digest'] != request.digest:
            raise PoiseError('Migration request_id belongs to another intent')
        values.append(item)
    return values


def _record(db, adapter, request, value):
    db.execute('INSERT INTO journal(at,session_id,task_id,event,data) VALUES(?,?,?,?,?)',
               (adapter._audit_utc(), 'task-workflow-migrate', None, EVENT,
                canonical({'request_id': request.request_id, 'request_digest': request.digest, **value})))


def _owner(root, config, goal):
    if 'task_planning' not in config:
        raise PoiseError('Migration requires the configured publication owner')
    path = Path(config['task_planning']['catalogue'])
    if not path.is_absolute():
        path = root / path
    catalogue = FileCatalogue(path)
    target = descendant(root, config['workflows'][goal])
    if catalogue.editor.target(goal) != target:
        raise PoiseError('Configured workflow and registered publication target differ')
    return FileGoalConfigRepository(catalogue.editor)


def _configuration_binding(root, config, project_owner=None):
    state = configured_root(root, config['paths']['state'])
    resources = {}
    for key in ('database', 'lock'):
        path = descendant(state, config['paths'][key])
        stat = path.stat()
        resources[key] = {'path': str(path), 'device': stat.st_dev, 'inode': stat.st_ino}
    if project_owner is not None:
        return {'root': str(root), 'manifest_digest': identity(config), 'resources': resources,
                'project': project_owner.stage_criteria_binding(root / project_owner.settings.raw['manifest'])}
    path = Path(config['task_planning']['catalogue'])
    catalogue = FileCatalogue(path if path.is_absolute() else root / path)
    return {'root': str(root), 'manifest_digest': identity(config), 'resources': resources,
            'catalogue_path': str(catalogue.path), 'catalogue_digest': identity(catalogue.raw),
            'publications': {goal: _owner(root, config, goal).migration_binding(goal)
                             for goal in config['workflows']}}


def _require_configuration(adapter, intent):
    """Reread actual files; a cached manifest cannot observe another writer."""
    root, config, _ = load_workflow_migration_config(adapter.config_path)
    owner = adapter.project_publication
    observed = _configuration_binding(root, config, owner)
    if owner is not None and observed.get('project') != intent['configuration_binding'].get('project'):
        raise PoiseError('Migration publication owner changed')
    if observed != intent['configuration_binding']:
        raise PoiseError('Migration configuration changed after preparation')
    if owner is not None:
        owner.require_stage_criteria_source(intent['project_publication'])


def _prepare(db, root, config, workflows, request, backup_snapshot, project_owner=None):
    tasks, events, submissions = _records(db)
    selected = [row for row in tasks if row['status'] not in ('completed', 'cancelled')]
    wanted = {('workflow', key) for key in workflows}
    wanted.update(('task', row['id']) for row in selected)
    actual = {('workflow', item['goal_type']) if item['kind'] == 'workflow_definition'
              else ('task', item['task_id']) for item in request.mapping}
    if wanted != actual:
        raise PoiseError('Mapping does not match the complete workflow and nonterminal Task inventory')
    baseline = _snapshot(db)
    if baseline != backup_snapshot:
        raise PoiseError('Task DB differs from the named backup')
    receipt = identity({'request_digest': request.digest, 'source_database': baseline})
    publication = project_owner.stage_criteria_snapshot(root / project_owner.settings.raw['manifest']) if project_owner else None
    prepared = []
    for mapping in request.mapping:
        if mapping['kind'] == 'workflow_definition':
            goal = mapping['goal_type']
            source = workflows[goal]
            old = identity(source)
            if publication is not None:
                if mapping['expected_revision'] != old:
                    raise PoiseError('Workflow source revision mismatch')
                observed = {'bytes': publication['bytes'][goal]}
                binding = publication['binding']
            else:
                owner = _owner(root, config, goal)
                observed = owner.snapshot_for_migration(goal)
                if (observed['pending'] or observed['head'] != old
                        or observed['document'] != source or mapping['expected_revision'] != old):
                    raise PoiseError('Historical workflow must match the exact managed editor head')
                binding = owner.migration_binding(goal)
            try:
                candidate = workflow_candidate(source, mapping['stages'])
            except ValueError as exc:
                if project_owner is not None:
                    raise PoiseError(str(exc)) from exc
                raise
            GoalTypeDefinition.from_configuration(candidate)
            summary = {'kind': 'workflow_definition', 'goal_type': goal,
                       'old_digest': old, 'new_digest': identity(candidate),
                       'old_revision': old, 'new_revision': identity(candidate)}
            prepared.append({'mapping': mapping, 'source': source, 'candidate': candidate,
                             'source_bytes_digest': identity(list(observed['bytes'])),
                             'publication_binding': binding,
                             'summary': summary})
        else:
            matches = [row for row in selected if row['id'] == mapping['task_id']]
            if len(matches) != 1:
                raise PoiseError('Migration Task source is missing')
            row = matches[0]
            if mapping['kind'] == 'newborn_draft':
                candidate, summary = draft_candidate(row, mapping)
                if 'workflow' in candidate:
                    GoalTypeDefinition.from_configuration(candidate['workflow'])
            else:
                candidate, summary = task_candidate(row, mapping, events, submissions, receipt)
                GoalTypeDefinition.from_configuration(candidate['workflow'])
                StageCriteriaBook.from_dict(candidate['stage_criteria'])
            prepared.append({'mapping': mapping, 'source': row,
                             'candidate': candidate, 'summary': summary})
    intent = {'phase': 'intent', 'receipt': receipt, 'baseline': baseline,
              'manifest_digest': identity(config),
              'configuration_binding': _configuration_binding(root, config, project_owner),
              'resources': prepared}
    if publication is not None:
        source = project_owner.stage_criteria_source(
            root / project_owner.settings.raw['manifest'], publication['revision'])
        candidate = {item['mapping']['goal_type']: item['candidate'] for item in prepared
                     if item['mapping']['kind'] == 'workflow_definition'}
        child = identity({'migration': request.digest, 'owner': publication['binding'],
                          'source': source, 'candidate': candidate})
        intent['project_publication'] = {
            'config_path': str(root / project_owner.settings.raw['manifest']),
            'binding': publication['binding'], 'config': config,
            'source': source,
            'workflows': workflows, 'candidate': candidate, 'revision': publication['revision'],
            'request': {'schema': 'project-config-update-1', 'request_id': child,
                        'config_path': str(root / project_owner.settings.raw['manifest']),
                        'expected_revision': publication['revision'], 'manifest_edits': [],
                        'workflow_updates': [], 'state_relocation': None, 'probe_repository': False,
                        'receipt_path': f'operations/stage-criteria-{child}.json'}}
    return intent


def _publication_request(request, item):
    # A deterministic child identity in the existing editor journal, not another API.
    value = {'request_id': identity({'migration': request.digest,
                                   'goal_type': item['mapping']['goal_type']}),
             'changes': []}
    digest = identity({'request': value, 'candidate': item['candidate'],
                       'source_revision': item['summary']['old_revision']})
    return value, digest


def _require_workflow(root, config, request, item, *, completed):
    goal = item['mapping']['goal_type']
    owner = _owner(root, config, goal)
    observed = owner.snapshot_for_migration(goal)
    expected = item['summary']['new_revision'] if completed else item['summary']['old_revision']
    if completed:
        packet, digest = _publication_request(request, item)
        # Exact editor replay also checks receipt/path/candidate/head/pending identity.
        if (observed['document'] != item['candidate'] or observed['head'] != expected
                or observed['pending']):
            raise PoiseError('Published migration workflow drifted')
        receipt = owner.completed_migration(goal, packet, digest,
                                            item['summary']['old_revision'], item['candidate'])
        if receipt is None:
            raise PoiseError('Migration publication is not completed')
        return receipt
    if (identity(observed['document']) != expected or observed['head'] != expected
            or identity(list(observed['bytes'])) != item['source_bytes_digest']
            or observed['pending']):
        raise PoiseError('Historical migration workflow drifted')
    return owner


def _completed_resources(request, intent, known):
    """Retain verified durable observations; inspect unobserved original owners.

    Later file drift blocks recovery, but cannot erase an already observed
    completed publication. Unobserved effects require fresh owner validation.
    """
    result = []
    for item in intent['resources']:
        if item['mapping']['kind'] != 'workflow_definition':
            continue
        if item['summary'] in known:
            result.append(item['summary'])
            continue
        goal = item['mapping']['goal_type']
        try:
            owner = FileGoalConfigRepository.from_migration_binding(goal, item['publication_binding'])
            packet, digest = _publication_request(request, item)
            receipt = owner.completed_migration(goal, packet, digest,
                                                item['summary']['old_revision'], item['candidate'])
            if receipt is not None:
                result.append(item['summary'])
        except (PoiseError, OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            # Unknown/changed original owner is not a verified external effect.
            continue
    return result


def _continue(db, adapter, root, config, request, intent, saved):
    known = [item['resource'] for item in saved if item['phase'] == 'published']
    try:
        _require_configuration(adapter, intent)
        if _snapshot(db) != intent['baseline']:
            raise PoiseError('Migration source inventory changed after preparation')
        workflows = [item for item in intent['resources'] if item['mapping']['kind'] == 'workflow_definition']
        for item in workflows:
            _require_configuration(adapter, intent)
            done = item['summary'] in known
            if done:
                _require_workflow(root, config, request, item, completed=True)
                continue
            goal = item['mapping']['goal_type']
            owner = _owner(root, config, goal)
            packet, digest = _publication_request(request, item)
            # This resumes only the exact known owner intent, including a crash
            # after file replacement but before our publication observation.
            owner.publish_migration(goal, packet, digest,
                                    item['summary']['old_revision'], item['candidate'])
            _require_workflow(root, config, request, item, completed=True)
            known.append(item['summary'])
            _record(db, adapter, request, {'phase': 'published', 'resource': item['summary']})
        _require_configuration(adapter, intent)
        return _finish_tasks(db, adapter, root, config, request, intent, workflows)
    except (PoiseError, OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        return {'status': 'recovery_required', 'request_id': request.request_id,
                'receipt': intent['receipt'], 'resources': _completed_resources(request, intent, known),
                'reason': str(exc)}


def _untyped_draft(item):
    return item['mapping']['kind'] == 'newborn_draft' and 'workflow' not in item['candidate']


def _finish_tasks(db, adapter, root, config, request, intent, workflows):
    db.execute('BEGIN IMMEDIATE')
    try:
        _require_configuration(adapter, intent)
        if _snapshot(db) != intent['baseline']:
            raise PoiseError('Task DB drifted before the migration transaction')
        for item in workflows:
            if adapter.project_publication is None:
                _require_workflow(root, config, request, item, completed=True)
            elif adapter.project_publication.completed_stage_criteria(intent['project_publication']) is None:
                raise PoiseError('Migration publication is not completed')
        for item in intent['resources']:
            if item['mapping']['kind'] == 'workflow_definition':
                continue
            source = item['source']
            current = db.execute(
                'SELECT id,status,stage_index,iteration,claimed_by,version,current_submission_id,metadata '
                'FROM tasks WHERE id=?', (source['id'],)).fetchone()
            if dict(current) != source:
                raise PoiseError('Task source changed during migration')
            if _untyped_draft(item):
                continue
            db.execute('UPDATE tasks SET metadata=? WHERE id=?',
                       (canonical(item['candidate']), source['id']))
            adapter.after_update(source['id'])
        for item in intent['resources']:
            if item['mapping']['kind'] != 'workflow_definition':
                raw = db.execute('SELECT metadata FROM tasks WHERE id=?',
                                 (item['source']['id'],)).fetchone()[0]
                expected = item['source']['metadata'] if _untyped_draft(item) else item['candidate']
                actual = raw if _untyped_draft(item) else json.loads(raw)
                if actual != expected:
                    raise PoiseError('Migration Task readback mismatch')
        result = {'status': 'migrated', 'request_id': request.request_id,
                  'resources': [item['summary'] for item in intent['resources']],
                  'receipt': intent['receipt'], 'replayed': False}
        _record(db, adapter, request, {'phase': 'completed', 'result': result})
        db.execute('COMMIT')
        return result
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise


def _continue_project(db, adapter, root, config, request, intent, saved):
    owner = adapter.project_publication
    publication = intent['project_publication']
    workflows = [item for item in intent['resources'] if item['mapping']['kind'] == 'workflow_definition']
    known = [item['resource'] for item in saved if item['phase'] == 'published']
    try:
        _require_configuration(adapter, intent)
        if _snapshot(db) != intent['baseline']:
            raise PoiseError('Migration source inventory changed after preparation')
        owner.publish_stage_criteria(publication)
        if owner.completed_stage_criteria(publication) is None:
            raise PoiseError('Migration publication is not completed')
        for item in workflows:
            if item['summary'] not in known:
                known.append(item['summary'])
                _record(db, adapter, request, {'phase': 'published', 'resource': item['summary']})
        return _finish_tasks(db, adapter, root, config, request, intent, workflows)
    except (PoiseError, OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        observed = list(known)
        try:
            if owner.completed_stage_criteria(publication) is not None:
                observed = [item['summary'] for item in workflows]
        except (PoiseError, OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            pass
        return {'status': 'recovery_required', 'request_id': request.request_id,
                'receipt': intent['receipt'], 'resources': observed, 'reason': str(exc)}


def migrate_stage_criteria(adapter, request):
    try:
        root, config, workflows = load_workflow_migration_config(adapter.config_path)
        state = configured_root(root, config['paths']['state'])
        database = descendant(state, config['paths']['database'])
        lock = descendant(state, config['paths']['lock'])
        backup = database.parent / 'backups' / request.backup_name
        if not database.is_file() or not backup.is_file():
            raise PoiseError('Migration requires the existing Task DB and named backup')
        with ExitStack() as locks:
            if adapter.project_publication is not None:
                locks.enter_context(adapter.project_publication.stage_criteria_lock())
            locks.enter_context(exclusive_lock(lock, config['limits']['lock_seconds'],
                                                config['limits']['lock_poll_seconds']))
            with closing(sqlite3.connect(_read_only_uri(backup), uri=True)) as source:
                if source.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise PoiseError('Named migration backup failed integrity_check')
                backup_snapshot = _snapshot(source)
            with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True,
                                         timeout=0, isolation_level=None, autocommit=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA foreign_keys=ON')
                saved = _saved(db, request)
                original_intents = [entry for entry in saved if entry['phase'] == 'intent']
                if (original_intents and 'project_publication' in original_intents[0]
                        and adapter.project_publication is None):
                    raise PoiseError('Migration publication owner changed')
                completed = [entry for entry in saved if entry['phase'] == 'completed']
                if completed:
                    if adapter.project_publication is not None:
                        original = next(entry for entry in saved if entry['phase'] == 'intent')
                        _require_configuration(adapter, original)
                        if adapter.project_publication.completed_stage_criteria(original['project_publication']) is None:
                            raise PoiseError('Migration publication is not completed')
                    return {**completed[-1]['result'], 'replayed': True}
                intents = [entry for entry in saved if entry['phase'] == 'intent']
                if intents:
                    if len(intents) != 1 or intents[0]['baseline'] != backup_snapshot:
                        raise PoiseError('Migration recovery source does not match its intent')
                    intent = intents[0]
                    if adapter.project_publication is not None:
                        _require_configuration(adapter, intent)
                else:
                    intent = _prepare(db, root, config, workflows, request, backup_snapshot, adapter.project_publication)
                    adapter.after_preflight()
                    _require_configuration(adapter, intent)
                    # No intent/publication is durable before this drift check.
                    db.execute('BEGIN IMMEDIATE')
                    try:
                        _require_configuration(adapter, intent)
                        if _snapshot(db) != intent['baseline']:
                            raise PoiseError('Task DB differs after migration preflight')
                        for item in intent['resources']:
                            if item['mapping']['kind'] == 'workflow_definition' and adapter.project_publication is None:
                                _require_workflow(root, config, request, item, completed=False)
                        _record(db, adapter, request, intent)
                        db.execute('COMMIT')
                    except BaseException:
                        if db.in_transaction:
                            db.execute('ROLLBACK')
                        raise
                if adapter.project_publication is not None:
                    return _continue_project(db, adapter, root, config, request, intent, saved)
                return _continue(db, adapter, root, config, request, intent, saved)
    except PoiseError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise PoiseError(f'Stage criteria migration rejected: {exc}') from exc
