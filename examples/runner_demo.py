"""Real CLI/SQLite/Git route demo. Reviews and user decisions are fixtures, not LLM evaluations."""
from __future__ import annotations
from copy import deepcopy
from work_client import WorkClient
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from demo import create, save, git, SOURCE

def run_one(directory: Path, goal: str, feedback: bool):
    home = create(directory)
    process = json.loads((SOURCE / f'config/processes/{goal}.json').read_text())
    save(home / f'config/processes/{goal}.json', process)
    cfg = json.loads((home / 'project.json').read_text())
    cfg['processes'] = {goal: f'config/processes/{goal}.json'}
    cfg['automatic_checks'] = []
    save(home / 'project.json', cfg)
    if goal == 'development':
        task = json.loads((home / 'task.json').read_text())
        task['artifact_requirements'] = []
        task['checks'] = {s['id']: ['RED'] if s['id'] in ('tests', 'test_fix') else ['GREEN'] if s['id'] in ('implementation', 'code_review', 'code_fix', 'code_recheck') else [] for s in process['stages']}
    else:
        task = json.loads((SOURCE / 'examples/documentation-task.example.json').read_text())
        task['methods'][0]['argv'][0] = sys.executable
    task['evidence_plan'] = {s['id']: {'subject_methods': {}, 'arguments': [], 'review_arguments': []} for s in process['stages']}
    save(home / 'task.json', task)
    env = {**os.environ, 'PYTHONPATH': str(SOURCE / 'src'), 'POISE_CONFIG': str(home / 'project.json'), 'POISE_SESSION': 'runner-demo'}
    calls = []
    client = WorkClient(env, 30)
    calls = client.calls
    artifact_specs = []
    ctx = client.bootstrap(json.loads(Path(str(home / 'task.json')).read_text()))
    if goal == 'development':
        route = [('tests', {}), ('test_review', {'coverage': 'Проверены oracle и RED.', 'findings': [], 'resolution_decisions': []}), ('implementation', {}), ('code_review', None)]
        review_id, fix_id, follow_id = ('code_review', 'code_fix', 'code_recheck')
    else:
        route = [('draft', {}), ('editorial', None)]
        review_id, fix_id, follow_id = ('editorial', 'amend', 'editorial_followup')
    issue = {'id': 'F1', 'subject': 'result', 'description': 'Нужна ясная обработка/описание отрицательного аргумента.', 'evidence': 'Обнаружено осмотром; input задаётся сценарным драйвером.'}
    clean = {'coverage': 'Объект осмотрен по критериям.', 'findings': [], 'resolution_decisions': []}
    route[-1] = (review_id, {**clean, 'findings': [issue]} if feedback else clean)
    if feedback:
        for number, verdict in [(1, 'rejected'), (2, 'accepted')]:
            route.extend([(fix_id, {'resolutions': [{'id': f'R{number}', 'finding_id': 'F1', 'description': f'Предложение {number}', 'evidence': 'Точный проектный check выполняет Poise; смысл осматривается агентом.'}]}), (follow_id, {**clean, 'resolution_decisions': [{'resolution_id': f'R{number}', 'decision': verdict, 'reason': 'Результат повторного осмотра, заданный сценарием.'}]})])
    reports = []
    for index, (stage, work) in enumerate(route):
        if index:
            ctx = client.bootstrap(None, decision='continue')
        assert ctx['stage'] == stage, (stage, ctx)
        wt = Path(ctx['worktree'])
        if stage == 'tests':
            (wt / 'tests').mkdir()
            (wt / 'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2),4)\n')
        if stage in ('implementation', 'code_fix'):
            (wt / 'src/double.py').write_text(f'def double(n):\n    return n * 2\n# Iteration {index}\n')
        if stage in ('draft', 'amend'):
            (wt / 'docs').mkdir(exist_ok=True)
            (wt / 'docs/guide.md').write_text(f'# Guide\nCall double(n); returns n * 2.\nReview iteration {index}.\n')
        payload = deepcopy(ctx['result_template'])
        payload['sections']['report'] = f'Пример этапа {stage}. Осмотр и решения пользователя — тестовые данные.'
        payload['commit_message'] = f'demo: {goal} {stage} {index}'
        payload['stage_work'] = work
        report = client.verify(payload, artifact_specs)
        assert report['status'] == 'verified'
        assert client.content()['stage'] == stage
        reports.append(report)
    final = client.accept()
    assert final['status'] == 'completed'
    branch = git(wt, 'symbolic-ref', '--short', 'HEAD')
    sha = git(wt, 'rev-parse', 'HEAD')
    assert git(wt, 'ls-remote', 'backup', f'refs/heads/{branch}').split()[0] == sha
    unchanged = git(directory / 'application', 'show', 'main:src/double.py').endswith('return n+1')
    assert unchanged
    data = {'goal_type': goal, 'feedback': feedback, 'status': 'PASS', 'stages': [x['stage'] for x in reports], 'outcomes': [x['stage_outcome'] for x in reports], 'iterations': [x['iteration'] for x in reports], 'task_status': final['status'], 'final_commit': sha, 'main_unchanged': unchanged, 'calls': calls}
    save(directory / 'runner-report.json', data)
    return {k: v for k, v in data.items() if k != 'calls'}

def run(directory: Path):
    directory.mkdir(parents=True, exist_ok=False)
    results = []
    for goal in ('development', 'documentation'):
        for feedback in (False, True):
            results.append(run_one(directory / f"{goal}-{('feedback' if feedback else 'short')}", goal, feedback))
    result = {'status': 'PASS', 'routes': results}
    save(directory / 'runner-report.json', result)
    return result
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.directory.resolve()), ensure_ascii=False, indent=2))
