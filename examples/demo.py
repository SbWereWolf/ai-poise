"""Автономный пример. Создаёт ТОЛЬКО новый каталог и локальные Git repositories."""
from __future__ import annotations
from copy import deepcopy
from work_client import WorkClient
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
SOURCE = Path(__file__).resolve().parents[1]

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True, stderr=subprocess.STDOUT).strip()

def create(directory: Path):
    directory.mkdir(parents=True, exist_ok=False)
    app = directory / 'application'
    app.mkdir()
    git(app, 'init', '-b', 'main')
    git(app, 'config', 'user.name', 'Demo')
    git(app, 'config', 'user.email', 'demo@example.invalid')
    (app / 'src').mkdir()
    (app / 'src/double.py').write_text('def double(n):\n    return n+1\n')
    (app / 'AGENTS.md').write_text('Использовать unittest; менять только заявленную область задачи.\n')
    git(app, 'add', '.')
    git(app, 'commit', '-m', 'Initial demo')
    remote = directory / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    git(app, 'remote', 'add', 'backup', str(remote))
    git(app, 'push', 'backup', 'main')
    home = directory / 'harness'
    home.mkdir()
    stages = []
    for sid, ro, paths in [('tests', False, ['tests/**']), ('test_review', True, []), ('implementation', False, ['src/**']), ('code_review', True, [])]:
        stages.append({'id': sid, 'instruction': f'Выполнить только этап {sid}, затем доклад пользователю.', 'read_only': ro, 'allowed_paths': paths, 'normalization': 'strip', 'sections': {'report': 'Заполнить результат этапа.'}, 'required_sections': ['report'], 'artifact_requirements': []})
    for i, stage in enumerate(stages):
        stage.update(handler='produce', transitions={'complete': stages[i + 1]['id'] if i + 1 < len(stages) else None}, rework_targets=[stage['id']])
    save(home / 'config/processes/development.json', {'route': {'entry': 'tests', 'max_transitions': 40, 'max_stage_visits': 8}, 'goal_type': 'development', 'benefit': {'git_categories':['code','documentation'],'sections':[]}, 'stages': stages, 'content_contract': {'sections': [], 'routes': [], 'requirements': []}})
    cfg = {'schema': 'ddd-accounting-11', 'project': 'demo', 'paths': {'state': 'state', 'database': 'state.sqlite', 'lock': 'state.lock', 'runtime': 'runtime', 'tasks': 'tasks', 'sprints': 'sprints', 'worktrees': 'worktrees', 'git_index': 'snapshot.index', 'runs': 'runs', 'stdout': 'stdout.txt', 'stderr': 'stderr.txt', 'response': 'response.json'}, 'limits': {'lock_seconds': 2.0, 'lock_poll_seconds': 0.01, 'git_seconds': 15.0, 'verify_attempts': 5, 'output_chars': 2200, 'preview_chars': 300}, 'git': {'repository': str(app), 'base_ref': 'main', 'remote': 'backup', 'branch_template': 'tasks/{task_id}', 'commit_pattern': '.+', 'author_name': 'Demo agent', 'author_email': 'demo-agent@example.invalid', 'push_required': True}, 'processes': {'development': 'config/processes/development.json'}, 'environment_names': ['PATH', 'HOME'], 'automatic_checks': [{'paths': ['src/**', 'tests/**'], 'by_stage': {'tests': ['RED'], 'test_review': [], 'implementation': ['GREEN'], 'code_review': []}}]}
    cfg['accounting'] = json.loads((SOURCE / 'config/accounting.example.json').read_text())
    cfg['sprint'] = json.loads((SOURCE / 'config/sprint.example.json').read_text())
    cfg['runtime_services'] = json.loads((SOURCE / 'config/runtime.example.json').read_text())
    cfg['batch'] = json.loads((SOURCE / 'config/batch.example.json').read_text())
    save(home / 'project.json', cfg)
    argv = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    methods = []
    for sid, code, needles in [('RED', 1, ['test_double', 'AssertionError: 3 != 4', 'Ran 1 test']), ('GREEN', 0, ['test_double', 'Ran 1 test', 'OK'])]:
        methods.append({'id': sid, 'argv': argv, 'cwd': '.', 'environment': {'LANG': 'C.UTF-8'}, 'timeout_seconds': 10, 'expected_exit_code': code, 'stdout_contains': [], 'stderr_contains': needles})
    task = {'id': 'DEMO-1', 'sprint_id': None, 'goal_type': 'development', 'goal': 'Исправить double(2), получить 4.', 'requirements': ['double(n) возвращает n*2'], 'definition_of_done': ['Регрессионный тест RED до правки, GREEN после.'], 'methods': methods, 'checks': {'tests': ['RED'], 'test_review': [], 'implementation': ['GREEN'], 'code_review': ['GREEN']}, 'artifact_requirements': [{'scope': 'task', 'pattern': 'artifacts/result.md', 'minimum': 1, 'maximum': 1}], 'content_contract': {'sections': [], 'routes': [], 'requirements': []}}
    task['evidence_plan'] = {s['id']: {'subject_methods': {}, 'arguments': [], 'review_arguments': []} for s in stages}
    save(home / 'task.json', task)
    return home

def run_demo(directory: Path) -> dict:
    home = create(directory)
    env = {**os.environ, 'PYTHONPATH': str(SOURCE / 'src'), 'HARNESS_CONFIG': str(home / 'project.json'), 'HARNESS_SESSION': 'demo-agent'}
    calls = []
    client = WorkClient(env, 30)
    calls = client.calls
    artifact_specs = []
    boot = client.bootstrap(json.loads(Path(str(home / 'task.json')).read_text()))
    reports = []
    for idx, sid in enumerate(['tests', 'test_review', 'implementation', 'code_review']):
        if idx:
            boot = client.bootstrap(None, decision='continue')
        assert boot['stage'] == sid
        wt = Path(boot['worktree'])
        if sid == 'tests':
            (wt / 'tests').mkdir()
            (wt / 'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2), 4)\n')
        if sid == 'implementation':
            (wt / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
        payload = deepcopy(boot['result_template'])
        payload['sections']['report'] = f'Демонстрационный результат этапа {sid}; это fixture, не реальный осмотр проекта.'
        payload['commit_message'] = f'demo: {sid}'
        if sid == 'code_review':
            artifact_specs = [{'scope': 'task', 'path': 'result.md', 'source': {'kind': 'text', 'text': 'Учебный результат: double(2)=4; проверены RED и GREEN.\n'}}]
        report = client.verify(payload, artifact_specs)
        assert report['status'] == 'verified'
        reports.append(report)
    accepted = client.accept()
    assert accepted['status'] == 'completed'
    result = {'status': 'PASS', 'root': str(directory), 'stages': [r['stage'] for r in reports], 'red_exit': reports[0]['checks'][0]['actual_exit_code'], 'green_exit': reports[2]['checks'][0]['actual_exit_code'], 'base_branch_unchanged': git(directory / 'application', 'show', 'main:src/double.py').endswith('return n+1'), 'result_commit': reports[-1]['commit'], 'artifacts': reports[-1]['artifacts'], 'task_status': accepted['status'], 'calls': calls}
    save(directory / 'demo-report.json', result)
    return result
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    args = parser.parse_args()
    result = run_demo(args.directory.resolve())
    print(json.dumps({k: v for k, v in result.items() if k != 'calls'}, ensure_ascii=False, indent=2))
