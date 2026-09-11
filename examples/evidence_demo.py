"""DDD-04: real CLI/SQLite/Git commands; arguments and review decisions are fixture inputs."""
from __future__ import annotations
from copy import deepcopy
from work_client import WorkClient
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from demo import create, save, SOURCE

def run(directory: Path, scenario: str):
    home = create(directory)
    observer = scenario == 'observe_feedback'
    goal = 'observation_demo' if observer else 'verification_demo'
    process = json.loads((SOURCE / f'config/processes/{goal}.json').read_text())
    save(home / f'config/processes/{goal}.json', process)
    cfg = json.loads((home / 'project.json').read_text())
    cfg['processes'] = {goal: f'config/processes/{goal}.json'}
    cfg['automatic_checks'] = []
    save(home / 'project.json', cfg)
    logical = scenario == 'logical'
    argument_required = scenario != 'command_negative'
    counter = home / 'command-count.txt'
    command = f"""from pathlib import Path; p=Path({str(counter)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x"); print("VALUE=3"); raise SystemExit({(1 if scenario == 'command_negative' else 0)})"""
    method = {'id': 'MEASURE', 'argv': [sys.executable, '-B', '-c', command], 'cwd': '.', 'environment': {}, 'source_under_test': {'kind': 'external', 'reason': 'The observer records generated evidence and reads no repository source.'}, 'timeout_seconds': 10, 'expected_exit_code': 0, 'stdout_contains': ['VALUE=3'], 'stderr_contains': []}
    task = json.loads((home / 'task.json').read_text())
    task.update(goal_type=goal, goal='Получить воспроизводимое наблюдение/аргумент и осмотреть его.', requirements=['Результат относится к выбранному состоянию и воспроизводим.'], definition_of_done=['Аргумент, когда требуется, осмотрен; отрицательный предметный результат не скрыт.'], methods=[] if logical else [method], checks={'measure': [] if logical else ['MEASURE'], 'audit': []}, artifact_requirements=[], evidence_plan={'measure': {'subject_methods': {} if logical else {'MEASURE': {'exit_codes': [0, 1], 'stdout_contains': ['VALUE=3'], 'stderr_contains': []}}, 'arguments': [{'id': 'A', 'kind': 'logical', 'phase': 'prepare' if logical else 'continue', 'observation_methods': [] if logical else ['MEASURE']}] if argument_required else [], 'review_arguments': []}, 'audit': {'subject_methods': {}, 'arguments': [], 'review_arguments': ['A'] if argument_required else []}})
    save(home / 'task.json', task)
    env = {**os.environ, 'PYTHONPATH': str(SOURCE / 'src'), 'POISE_CONFIG': str(home / 'project.json'), 'POISE_SESSION': 'evidence-demo'}
    calls = []
    client = WorkClient(env, 30)
    calls = client.calls
    artifact_specs = []

    def proposal(ids, iteration):
        return {'id': 'A', 'kind': 'logical', 'facts': ['Наблюдение содержит VALUE=3.' if ids else 'В задаче явно задано X=3.'], 'assumptions': [], 'inference': f'По определению положительного X значение 3 положительно. Подход {iteration}.', 'conclusion': 'Критерий положительности выполнен.', 'verdict': 'proved', 'observation_ids': ids}

    def submit(ctx, phase, arguments, decisions):
        value = deepcopy(ctx['result_template'])
        value['sections']['report'] = 'Аргумент/решение данного demo задаёт сценарий, не независимая модель.'
        value['evidence_work'] = {'phase': phase, 'arguments': arguments, 'decisions': decisions}
        if ctx['handler'] == 'inspect':
            value['stage_work'] = {'coverage': 'Осмотрены факты, предпосылки и вывод.', 'findings': [], 'resolution_decisions': []}
        return value
    ctx = client.bootstrap(json.loads(Path(str(home / 'task.json')).read_text()))
    reports = []
    for iteration in range(1, 3 if observer else 2):
        payload = submit(ctx, 'prepare', [proposal([], iteration)] if logical else [], [])
        record = client.verify(payload, artifact_specs)
        if argument_required and (not logical):
            assert record['status'] == 'awaiting_continuation'
            count_before = counter.read_text()
            payload = submit(record['context'], 'continue', [proposal([r['id'] for r in record['checks']], iteration)], [])
            record = client.verify(payload, artifact_specs)
            assert counter.read_text() == count_before
        assert record['status'] == 'verified'
        reports.append(record)
        ctx = client.bootstrap(None, decision='continue')
        decisions = []
        if argument_required:
            latest = record['evidence']['arguments'][-1]
            decisions = [{'argument_id': 'A', 'revision': latest['revision'], 'decision': 'rejected' if observer and iteration == 1 else 'accepted', 'reason': 'Решение осмотра задано сценарным драйвером.'}]
        payload = submit(ctx, 'prepare', [], decisions)
        review = client.verify(payload, artifact_specs)
        reports.append(review)
        if observer and iteration == 1:
            assert review['stage_outcome'] == 'changes_requested'
            ctx = client.bootstrap(None, decision='continue')
        else:
            assert review['stage_outcome'] == 'clear'
    final = client.accept()
    assert final['status'] == 'completed'
    receipts = [r for report in reports for r in report['checks']]
    assert all((Path(r['stdout']).is_file() and Path(r['stderr']).is_file() for r in receipts))
    result = {'status': 'PASS', 'scenario': scenario, 'task_status': 'completed', 'stage_outcomes': [r['stage_outcome'] for r in reports], 'command_executions': len(counter.read_text()) if counter.exists() else 0, 'arguments': len(final['evidence']['arguments']), 'decisions': len(final['evidence']['decisions']), 'raw_output_survives_cleanup': True, 'calls': calls}
    save(directory / 'evidence-report.json', result)
    return result
if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', required=True, type=Path)
    p.add_argument('--scenario', required=True, choices=['command_negative', 'logical', 'mixed', 'observe_feedback'])
    args = p.parse_args()
    print(json.dumps(run(args.directory.resolve(), args.scenario), ensure_ascii=False, indent=2))
