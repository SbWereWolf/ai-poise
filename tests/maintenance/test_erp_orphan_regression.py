"""R1/R3: terminal maintenance settles a leaf after intermediary failure."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

pytest.register_assert_rewrite('erp_support')
from erp_support import FIXTURES, action_argv, read_json, write_json
from test_erp_regressions import owned_process_state

pytest_plugins = ['erp_support']


def test_terminal_timeout_settles_leaf_after_intermediary_failure(erp_deployed):
    case = erp_deployed
    directory = case['root'] / 'ordinary orphan timeout'
    directory.mkdir()
    resources = read_json(case['profile'] / 'resources.json')
    selected = read_json(FIXTURES / 'erp_process_resource.json')
    selected['argv'][0] = sys.executable
    selected['argv'][2] = str(FIXTURES / 'erp_orphan_process.py')
    selected['argv'][3] = 'root'
    selected['argv'][4] = str(directory)
    selected['cwd'] = str(directory)
    resources['resources']['erp-php-runtime'] = selected
    write_json(case['profile'] / 'resources.json', resources)
    argv, cwd, env = action_argv(case, 'erp-php-runtime', 'check')
    unrelated = subprocess.Popen([
        sys.executable, '-B', str(FIXTURES / 'erp_owned_process.py'),
        'unrelated', str(directory),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
       start_new_session=True)
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=6)
        leaf_pid = int((directory / 'leaf-pid').read_text())
        terminal_state = owned_process_state(leaf_pid)
        terminated_at_return = terminal_state in ('terminated', 'Z', 'X')
        assert (directory / 'leaf-started').read_text() == 'ordinary orphan leaf started', 'Fixture leaf startup failed'
        assert (directory / 'middle-exit').read_text() == '1', 'Fixture root must reap ordinary intermediary failure'
        response = json.loads(result.stdout)
        assert response['state'] == 'unavailable' and response['cause'] == 'process', (result.stdout, result.stderr)
        assert 'ordinary orphan leaf started' in result.stderr
        assert 'ordinary root diagnostic' in result.stderr
        assert unrelated.poll() is None, 'Unrelated process must survive orphan timeout'
        (directory / 'release-leaf').touch()
        # Observation happens in this still-live host, before any runner teardown.
        time.sleep(0.5)
        late_effect = (directory / 'leaf-effect').exists()
        assert terminated_at_return, (
            'Owned orphan leaf remains live after terminal timeout',
            'late effect observed', late_effect)
        assert not late_effect, 'Timed-out orphan leaf performed a late effect'
    finally:
        (directory / 'release-leaf').touch()
        try:
            identity = directory / 'leaf-pid'
            if identity.exists():
                leaf_pid = int(identity.read_text())
                if owned_process_state(leaf_pid) not in ('terminated', 'Z', 'X'):
                    try:
                        os.kill(leaf_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=3)
