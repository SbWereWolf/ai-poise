"""Rejected packets retain complete results without writer admission."""
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys
import pytest
from batch.test_public_read_transactions import environment, invoke, \
    query_state, state_paths, writer


@pytest.mark.parametrize('prepared', [False, True])
@pytest.mark.parametrize('raw', [' ' * 31, '{'])
def test_precomposition_rejection_preserves_full_response_without_store_write(
        project, prepared, raw):
    if prepared:
        result = invoke(project, 'public-read-bootstrap.json')
        assert result.returncode == 0, result.stdout + result.stderr
    database, lock = state_paths(project)
    before = query_state(database) if prepared else None
    config = json.loads(project['config_path'].read_text())
    config['batch']['max_input_bytes'] = 30
    project['config_path'].write_text(json.dumps(config))
    with writer(project, 'local-lock') if prepared else nullcontext():
        result = subprocess.run(
            [sys.executable, '-B', '-m', 'poise', 'work'], input=raw,
            capture_output=True, text=True, env=environment(project),
            timeout=15)
        assert result.returncode == 2
        assert result.stdout, 'PRECOMPOSITION_REJECTION_LOST_COMPLETE_RESPONSE: ' + result.stderr
        assert len(result.stdout) <= config['limits']['output_chars']
        envelope = json.loads(result.stdout)
        full = json.loads(Path(envelope['response_path']).read_text())
        assert full['status'] == 'rejected'
        assert full['reason']
        if len(raw) == 31:
            assert 'max_input_bytes' in full['reason']
        if prepared:
            assert query_state(database) == before
        else:
            assert not database.exists()
            assert not lock.exists()
