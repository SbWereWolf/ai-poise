"""Staged content gates, two trace routes, failure/correction/rework, real CLI.

All inspection statements are deterministic example input, not AI peer review.
Creates only a new directory and a local bare Git remote; does not use network.
"""
from __future__ import annotations
from copy import deepcopy
from work_client import WorkClient
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from demo import create, save, git, SOURCE

def run(directory: Path) -> dict:
    fixture = json.loads((SOURCE / 'examples/content-demo-contract.json').read_text())
    home = create(directory)
    process_path = home / 'config/processes/development.json'
    process = json.loads(process_path.read_text())
    process['content_contract'] = fixture['goal_content']
    process['stages'][2]['allowed_paths'].append('docs/**')
    save(process_path, process)
    task_path = home / 'task.json'
    task = json.loads(task_path.read_text())
    task['content_contract'] = fixture['task_content']
    # Explicitly activate this example's predicates and actual writable scope.
    # The generic starter's empty refs are not equivalent to all catalog rules.
    predicates = fixture['goal_content']['requirements'] + fixture['task_content']['requirements']
    task['stage_contracts'] = [
        {'stage_id': stage['id'], 'allowed_paths': list(stage['allowed_paths']),
         'entry_requirements': [r['id'] for r in predicates
                                if stage['id'] in r['stages'] and r['phase'] == 'pre'],
         'exit_requirements': [r['id'] for r in predicates
                               if stage['id'] in r['stages'] and r['phase'] == 'post']}
        for stage in process['stages']
    ]
    save(task_path, task)
    # The scenario is self-contained, not a continuation of the invoking agent.
    binding = home / 'content-caller.json'
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('POISE_', 'CODEX_'))}
    env.update(PYTHONPATH=str(SOURCE / 'src'), POISE_CONFIG=str(home / 'project.json'),
               POISE_CALLER_BINDING=str(binding), LANG='C.UTF-8')
    calls = []
    client = WorkClient(env, 30)
    calls = client.calls
    artifact_specs = []
    boot = client.bootstrap(json.loads(Path(str(task_path)).read_text()), expected_exit=0)
    caller_bytes = binding.read_bytes()
    wt = Path(boot['worktree'])
    (wt / 'tests').mkdir()
    (wt / 'tests/test_double.py').write_text('import unittest\nfrom src.double import double\nclass Regression(unittest.TestCase):\n    def test_double(self):\n        self.assertEqual(double(2),4)\n')
    payload = deepcopy(boot['result_template'])
    payload['sections']['report'] = 'Regression test prepared.'
    payload['trace'] = fixture['initial_trace']
    payload['commit_message'] = 'demo: add regression test'
    blocked = client.verify(payload, artifact_specs, expected_exit=1)
    assert blocked['status'] == 'content_requirements_failed'
    assert [x['id'] for x in blocked['content_gate']['requirements'] if not x['passed']] == ['goal-notes']
    content_failures = [{'stage': blocked['stage'], 'phase': blocked['content_gate']['phase'],
                         'requirements': [r['id'] for r in blocked['content_gate']['requirements']
                                          if not r['passed']]}]
    payload['sections']['notes'] = 'The regression distinguishes +1 from multiplication by two.'
    red = client.verify(payload, artifact_specs, expected_exit=0)
    assert red['status'] == 'verified'
    assert red['checks'][0]['actual_exit_code'] == 1
    boot = client.bootstrap(None, expected_exit=0, decision='continue')
    payload = deepcopy(boot['result_template'])
    payload['sections']['report'] = 'Fixture inspection accepts the regression.'
    assert client.verify(payload, artifact_specs, expected_exit=0)['status'] == 'verified'
    boot = client.bootstrap(None, expected_exit=0, decision='continue')
    (wt / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    (wt / 'docs').mkdir()
    (wt / 'docs/double.md').write_text('# R1\n\nFor an integer n, double(n) returns n*2.\n')
    payload = deepcopy(boot['result_template'])
    payload['sections'].update(report='Function and product requirement documented.', edge_cases='Integer zero, negative and positive values.')
    payload['trace'] = fixture['implementation_trace']
    payload['commit_message'] = 'demo: implement and document double'
    green = client.verify(payload, artifact_specs, expected_exit=0)
    assert green['checks'][0]['actual_exit_code'] == 0
    boot = client.bootstrap(None, expected_exit=0, decision='continue')
    artifact_specs = [{'scope': 'task', 'path': 'result.md', 'source': {'kind': 'text', 'text': 'Result: multiplication by two; exact RED/GREEN methods recorded.\n'}}]
    payload = deepcopy(boot['result_template'])
    payload['sections']['report'] = 'Fixture code inspection accepts implementation.'
    payload['trace'] = fixture['review_trace']
    missing = client.verify(payload, artifact_specs, expected_exit=1)
    assert [r['id'] for r in missing['content_gate']['requirements'] if not r['passed']] == ['reason-later']
    content_failures.append({'stage': missing['stage'], 'phase': missing['content_gate']['phase'],
                             'requirements': [r['id'] for r in missing['content_gate']['requirements']
                                              if not r['passed']]})
    payload['trace'].update(fixture['proof'])
    assert client.verify(payload, artifact_specs, expected_exit=0)['status'] == 'verified'
    old_iteration = boot['iteration']
    feedback = 'Clarify the proof, preserving previous result.'
    boot = client.bootstrap(None, expected_exit=0, decision='rework', feedback=feedback)
    rework = {'stage': boot['stage'], 'from_iteration': old_iteration,
              'to_iteration': boot['iteration'], 'requested_feedback': feedback}
    assert rework['stage'] == 'code_review' and rework['to_iteration'] == old_iteration + 1
    payload = deepcopy(boot['result_template'])
    payload['sections']['report'] = 'Fixture repeat inspection with clarified proof.'
    payload['trace'] = fixture['proof']
    payload['trace']['reasoning']['proof']['inference'] = 'For every integer n, n+n equals n*2.'
    assert client.verify(payload, artifact_specs, expected_exit=0)['status'] == 'verified'
    # Capture the final verified content while this caller still owns the Task.
    # accept releases that selection; the public content query has no task_id.
    content = client.content(expected_exit=0)
    assert client.accept(expected_exit=0)['status'] == 'completed'
    result = {'status': 'PASS', 'calls': calls, 'task_status': 'completed', 'trace': content['trace'], 'base_unchanged': git(directory / 'application', 'show', 'main:src/double.py').endswith('return n+1'), 'doc_in_task_worktree': (wt / 'docs/double.md').is_file(), 'root': str(directory),
              'base_clean': not git(directory / 'application', 'status', '--porcelain'),
              'doc_absent_from_base': not (directory / 'application/docs/double.md').exists(),
              'caller_binding_unchanged': binding.read_bytes() == caller_bytes,
              'content_failures': content_failures, 'rework': rework}
    save(directory / 'content-demo-report.json', result)
    return result
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    args = parser.parse_args()
    result = run(args.directory.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
