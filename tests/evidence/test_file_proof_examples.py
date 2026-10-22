"""Bounded actual shipped consumers; public observer never changes responses."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('route', [
    'verification-short', 'verification-feedback', 'analysis-short', 'analysis-feedback'])
def test_catalogue_file_proofs_route(tmp_path, route):
    root = Path(__file__).resolve().parents[2]
    goal, scenario = route.split('-')
    directory = tmp_path / route
    env = {k: v for k, v in os.environ.items() if k not in (
        'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING', 'POISE_SESSION')}
    env.update(PYTHONPATH=str(root / 'src'), LANG='C.UTF-8')
    probe = Path(__file__).parent / 'fixtures/file_proofs/catalogue_probe.py'
    process = subprocess.run([sys.executable, '-B', str(probe), str(directory), goal, scenario],
        env=env, capture_output=True, text=True, timeout=180)
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads((directory / 'route-report.json').read_text())
    assert report['status'] == 'PASS' and report['task_status'] == 'completed'
    assert report['goal_type'] == goal and report['scenario'] == scenario
    assert report['open_findings_at_completion'] == 0 and report['remote_unchanged'] is True
    assert report['feedback_cycles'] == (0 if scenario == 'short' else 1)
    calls = json.loads((directory / 'public-call-witness.json').read_text())
    seen, paths, reads = {}, {}, {}
    total_reads = total_decisions = 0
    for call in calls:
        packet = call['packet']
        if packet['operation'] == 'show':
            queries = packet['input']['queries']
            for query, row in zip(queries, call['response']['results'], strict=True):
                if query['kind'] == 'proof_file':
                    proof = row['value']
                    identifier = query['argument_id']
                    assert identifier in seen
                    assert proof['path'] == paths[identifier]
                    assert proof['status'] == 'read_only' and proof['representation'] == 'text'
                    assert isinstance(proof['content'], str) and proof['content']
                    assert proof['size_bytes'] == len(proof['content'].encode('utf-8'))
                    reads[identifier] = True
                    total_reads += 1
        if packet['operation'] != 'verify':
            continue
        work = packet['input']['result']['evidence_work']
        for argument in work['arguments']:
            assert {'id', 'claim', 'path', 'covers', 'correction'} <= set(argument)
            assert not {'facts', 'inference', 'conclusion', 'body', 'revision'} & set(argument)
            identifier = argument['id']
            if identifier in paths:
                assert paths[identifier] == argument['path']
            paths[identifier] = argument['path']
            seen[identifier] = argument
            reads[identifier] = False
        for decision in work['decisions']:
            identifier = decision['argument_id']
            assert identifier in seen and reads.get(identifier) is True
            assert 'revision' not in decision
            total_decisions += 1
    assert seen and total_reads >= total_decisions > 0
