from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest


# The repository test command executes the sources from this exact worktree.
_TASK_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_TASK_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_TASK_SOURCE_ROOT))


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("LANG", "C.UTF-8")
    app = tmp_path / 'application'
    app.mkdir()
    git(app, 'init', '-b', 'main')
    git(app, 'config', 'user.name', 'Fixture')
    git(app, 'config', 'user.email', 'fixture@example.invalid')
    (app / 'src').mkdir()
    (app / 'src' / 'double.py').write_text('def double(n):\n    return n + 1\n')
    (app / 'AGENTS.md').write_text('Test command: python -m unittest discover -s tests -v\n')
    git(app, 'add', '.')
    git(app, 'commit', '-m', 'Initial application')
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    git(app, 'remote', 'add', 'backup', str(remote))
    git(app, 'push', 'backup', 'main')
    poise_root = tmp_path / 'poise'
    poise_root.mkdir()
    stages = []
    for name, readonly, allowed in [
        ('tests', False, ['tests/**']),
        ('test_review', True, []),
        ('implementation', False, ['src/**']),
        ('code_review', True, []),
    ]:
        stages.append({
            'id': name,
            'instruction': f'Выполнить один этап {name}.',
            'read_only': readonly,
            'allowed_paths': allowed,
            'normalization': 'strip',
            'sections': {'report': 'Заполнить результат этапа.'},
            'required_sections': ['report'],
            'artifact_requirements': [],
        })
    for i, stage in enumerate(stages):
        stage.update(handler="produce", transitions={"complete":stages[i+1]["id"] if i+1<len(stages) else None}, rework_targets=[stage["id"]])
    process = {'route':{"entry":"tests","max_transitions":40,"max_stage_visits":8}, 'goal_type': 'development', 'stages': stages, 'benefit': {'git_categories':['code','documentation'],'sections':[]}, "content_contract": {"sections":[],"routes":[],"requirements":[]}}
    write_json(poise_root / 'config/processes/development.json', process)
    cfg = {
        'schema': 'ddd-accounting-11',
        'project': 'demo',
        'paths': {
            'state': 'state', 'database': 'state.sqlite', 'lock': 'state.lock',
            'runtime': 'runtime', 'tasks': 'tasks', 'sprints': 'sprints',
            'worktrees': 'worktrees',
            'git_index': 'snapshot.index', 'runs': 'runs',
            'stdout': 'stdout.txt', 'stderr': 'stderr.txt', 'response': 'response.json',
        },
        'limits': {
            'lock_seconds': 2.0, 'lock_poll_seconds': 0.01,
            'git_seconds': 15.0, 'verify_attempts': 5,
            'output_chars': 2200, 'preview_chars': 300,
        },
        'git': {
            'repository': str(app), 'base_ref': 'main', 'remote': 'backup',
            'branch_template': 'tasks/{task_id}',
            'commit_pattern': '.+',
            'author_name': 'Agent fixture', 'author_email': 'agent@example.invalid',
            'push_required': True,
        },
        'processes': {'development': 'config/processes/development.json'},
        'environment_names': ['PATH', 'HOME', 'LANG'],
        'automatic_checks': [
            {'paths': ['src/**', 'tests/**'], 'by_stage': {
                'tests': ['RED'], 'test_review': [],
                'implementation': ['GREEN'], 'code_review': [],
            }},
        ],
    }
    cfg['accounting']=json.loads((Path(__file__).resolve().parents[1]/'config/accounting.example.json').read_text())
    cfg['runtime_services']=json.loads((Path(__file__).resolve().parents[1]/'config/runtime.example.json').read_text())
    cfg['batch']=json.loads((Path(__file__).resolve().parents[1]/'config/batch.example.json').read_text())
    cfg['sprint']=json.loads((Path(__file__).resolve().parents[1]/'config/sprint.example.json').read_text())
    cfg_path = write_json(poise_root / 'project.json', cfg)
    argv = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    methods = [
        {'id': 'RED', 'argv': argv, 'cwd': '.', 'environment': {},
         'timeout_seconds': 10, 'expected_exit_code': 1,
         'stdout_contains': [], 'stderr_contains': ['test_double', 'AssertionError: 3 != 4', 'Ran 1 test']},
        {'id': 'GREEN', 'argv': argv, 'cwd': '.', 'environment': {},
         'timeout_seconds': 10, 'expected_exit_code': 0,
         'stdout_contains': [], 'stderr_contains': ['test_double', 'Ran 1 test', 'OK']},
    ]
    task = {
        'id': 'T1', 'sprint_id': None, 'goal_type': 'development',
        'goal': 'Исправить double(2): получить 4.',
        'requirements': ['double(n) возвращает n*2'],
        'definition_of_done': ['Регрессионный тест RED до исправления и GREEN после.'],
        'methods': methods, 'artifact_requirements': [],
        'checks': {'tests': ['RED'], 'test_review': [], 'implementation': ['GREEN'], 'code_review': ['GREEN']},
     "content_contract": {"sections":[],"routes":[],"requirements":[]}}
    task['evidence_plan'] = {s['id']:{'subject_methods':{},'arguments':[],'review_arguments':[]} for s in stages}
    task_path = write_json(poise_root / 'task.json', task)
    return {'root': poise_root, 'config_path': cfg_path, 'cfg': cfg, 'process': process,
            'task_path': task_path, 'task': task, 'app': app, 'remote': remote}


def fill(result: dict, report='Этап выполнен и проверен.', artifacts=None):
    value=result['result_template']
    value['sections']['report']=report
    value['artifact_paths']=[] if artifacts is None else artifacts
    value['commit_message']='test: проверенный результат этапа'
    return value


def add_test(worktree):
    p = Path(worktree) / 'tests'
    p.mkdir(exist_ok=True)
    (p / 'test_double.py').write_text(
        'import unittest\nfrom src.double import double\n'
        'class Regression(unittest.TestCase):\n'
        '    def test_double(self):\n'
        '        self.assertEqual(double(2), 4)\n', encoding='utf-8')


# Test-only scenario client. It keeps the caller's draft in memory, then sends
# the NEW explicit result object. It is not installed/exported by Poise and
# it does not read or write an operational result file. This preserves old
# behavioural assertions while changing their transport fixture.
from poise.runtime import Poise as Runtime
from poise.modules.accounting.clock import ClockObservation
from copy import deepcopy
_SCENARIO_DRAFTS={}


class DeterministicClock:
    _audit=datetime(2026,9,7,tzinfo=timezone.utc)
    _monotonic_ns=0

    @classmethod
    def reset(cls):
        cls._audit=datetime(2026,9,7,tzinfo=timezone.utc);cls._monotonic_ns=0

    def observe(self):
        cls=type(self)
        value=ClockObservation(cls._audit.isoformat(),cls._monotonic_ns,'test-suite-boot')
        cls._audit+=timedelta(seconds=1);cls._monotonic_ns+=1_000_000_000
        return value


class WorkPoise(Runtime):
    """Modern work API wired to an explicit deterministic test clock."""
    def __init__(self,config_path,session,clock=None):
        super().__init__(config_path,session,DeterministicClock() if clock is None else clock)


@pytest.fixture(autouse=True)
def clear_scenario_clients():
    DeterministicClock.reset()
    _SCENARIO_DRAFTS.clear()
    yield
    _SCENARIO_DRAFTS.clear()

class Poise(Runtime):
    def __init__(self,config_path,session,clock=None):
        super().__init__(config_path,session,DeterministicClock() if clock is None else clock)

    def _remember(self,context):
        key=(str(self.config_path),self.session)
        candidate=context.get('result_template')
        if candidate is not None:
            old=_SCENARIO_DRAFTS.get(key)
            position=(context['task'],context['stage'],context['iteration'])
            if old is not None and old[0]==position:
                value=old[1]
                value.clear(); value.update(deepcopy(candidate));context['result_template']=value
            else:value=candidate
            _SCENARIO_DRAFTS[key]=(position,value)
        return context

    def bootstrap(self,task_file=None,decision=None,feedback=None,rework_stage=None):
        task=None if task_file is None else json.loads(Path(task_file).read_text())
        return self._remember(super().bootstrap(task,decision,feedback,rework_stage))

    def verify(self):
        draft=_SCENARIO_DRAFTS.get((str(self.config_path),self.session))
        r=super().verify(None if draft is None else deepcopy(draft[1]))
        if 'context' in r:self._remember(r['context'])
        return r
