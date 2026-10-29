"""Explicit bridge to the existing full Task configuration and session store."""
from pathlib import Path

from ..common import PoiseError, configured_root, descendant, read_json
from ..modules.projects.harness import TaskAdapterReference


class TaskHarnessAdapter:
    @staticmethod
    def observe_current(profile_path, profile, internal_session):
        """Read an existing genuine claim without establishing or acquiring work."""
        if profile['task_adapter'] is None:
            return {'state': 'not_applicable', 'reason': 'No Task adapter is connected'}
        import json
        from ..common import load_config
        from ..storage import Store
        from .sqlite.task_record import capture_record_in
        from .task_paths import task_root
        task_path = TaskHarnessAdapter.resolve_task_configuration(profile_path)
        root, cfg, workflows = load_config(task_path)
        state = configured_root(root, cfg['paths']['state'])
        store = Store(descendant(state, cfg['paths']['database']), descendant(state, cfg['paths']['lock']),
                      cfg['limits']['lock_seconds'], cfg['limits']['lock_poll_seconds'], workflows,
                      read_only=True, read_wait=cfg['limits']['sqlite_read_seconds'])
        current = store.current(internal_session)
        if current is None:
            return {'state': 'not_applicable', 'reason': 'The genuine native session has no current Task'}
        with store.database.read_transaction() as db:
            capture = capture_record_in(db, current['id'])
        if capture is None or capture.row['claimed_by'] != internal_session:
            return {'state': 'unavailable', 'reason': 'The observed Task claim changed during capture'}
        stage = current['workflow']['stages'][current['stage_index']]['id']
        workflow = json.loads(capture.row['workflow'])
        from ..modules.inspection.domain import FeedbackBook
        feedback = FeedbackBook.from_dict(workflow['feedback'])
        open_ids = {item.id for item in feedback.open_findings}
        phases = current['contract']['decomposition']['phases']
        skills = next((phase['skills'] for phase in phases if phase['stage'] == stage), [])
        return {'state': 'known', 'task_id': current['id'], 'task_status': current['status'],
                'phase': stage, 'sprint_id': current.get('sprint_id'),
                'task_path': str(task_root(state, cfg['paths'], current['id'], current.get('sprint_id'))),
                'required_skills': list(skills),
                'findings': [{'finding_id': item.id, 'finding_status': 'open' if item.id in open_ids else 'resolved'}
                             for item in feedback.findings],
                'task_binding_confidence': 1,
                'binding_reason': 'Current Task claim matches the genuine internal native session'}

    @staticmethod
    def resolve_task_configuration(path):
        path = Path(path).resolve()
        document = read_json(path)
        if document.get('schema') == 'ddd-accounting-12':
            return path
        from .harness_config import load_harness_config
        _, profile, _ = load_harness_config(path)
        reference = TaskAdapterReference.parse(profile['task_adapter'])
        if reference is None:
            raise PoiseError('task_adapter_required: connect an explicit stride-task-adapter-1')
        task_path = Path(reference.config_path).resolve()
        if task_path == path:
            raise PoiseError('task_adapter_mismatch: Task and general profiles must differ')
        TaskHarnessAdapter.validate(path, profile, task_path)
        return task_path

    @staticmethod
    def validate(profile_path, profile, task_path):
        from ..common import load_config
        from .projects import ProjectSettings, FileProjectSetup
        root, task, _ = load_config(task_path)
        if (profile['project'] != task['project']
                or Path(profile['git']['repository']).resolve() != Path(task['git']['repository']).resolve()):
            raise PoiseError('task_adapter_mismatch: project/repository differ')
        general_state = configured_root(Path(profile_path).parent, profile['paths']['state'])
        task_state = configured_root(root, task['paths']['state'])
        if (general_state != task_state or any(
                descendant(general_state, profile['paths'][key]) != descendant(task_state, task['paths'][key])
                for key in ('database', 'lock'))):
            raise PoiseError('task_adapter_mismatch: identity storage differs')
        settings = ProjectSettings(profile['runtime_resources']['project_setup'])
        entry = FileProjectSetup(settings)._registry()['projects'].get(profile['project'])
        if (entry is None or (settings.root / entry['config_path']).resolve() != Path(task_path).resolve()
                or (settings.root / entry.get('harness_config_path', '')).resolve() != Path(profile_path).resolve()):
            raise PoiseError('task_adapter_mismatch: restore the registered profile association')
        return root, task

    @staticmethod
    def resolve_harness_configuration(task_path):
        from ..common import load_config
        from .projects import ProjectSettings, FileProjectSetup
        task_path = Path(task_path).resolve()
        _, task, _ = load_config(task_path)
        settings = ProjectSettings(task['runtime_resources']['project_setup'])
        entry = FileProjectSetup(settings)._registry()['projects'].get(task['project'])
        if entry is None or (settings.root / entry['config_path']).resolve() != task_path:
            raise PoiseError('task_adapter_mismatch: Task association differs')
        if 'harness_config_path' not in entry:
            raise PoiseError('task_adapter_required: publish the explicit general profile association')
        return (settings.root / entry['harness_config_path']).resolve()

    @staticmethod
    def session_database(profile_path, profile):
        task_path = TaskHarnessAdapter.resolve_task_configuration(profile_path)
        from ..common import load_config
        from .session_establishment import _database
        root, cfg, _ = load_config(task_path)
        return _database(root, cfg, task_path, False)
