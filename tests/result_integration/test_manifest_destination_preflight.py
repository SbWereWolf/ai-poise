"""All genuinely selected integration methods preflight before candidate effects."""
from pathlib import Path

import pytest

from batch.helpers import request
from conftest import git
from poise.common import PoiseError
from result_integration.helpers import integration_input
from verification.retention_helpers import FIXTURES, consumer
from verification.retention_regression_helpers import node_snapshot, git_snapshot, semantic_snapshot, integration_ready


@pytest.mark.parametrize('entry', ['public', 'apply'])
@pytest.mark.parametrize('case', ['prefix', 'parent-file', 'symlink'])
def test_later_invalid_selected_method_prevents_all_integration_effects(project, tmp_path, case, entry):
    markers = [tmp_path / 'first.txt', tmp_path / 'second.txt']
    methods = []
    for index, marker in enumerate(markers):
        method = consumer(marker)
        method['id'] = f'INTEGRATION_{index}'
        method['argv'][-1] = (FIXTURES / 'retention_marker_consumer.py').read_text()
        method['outputs'] = []
        method['stdout_contains'] = ['consumer-completed']
        method['artifact_inputs'] = {'manifest_directory': 'artifacts/manifests', 'files': []}
        methods.append(method)
    if case == 'prefix':
        methods[1]['artifact_inputs']['manifest_directory'] = 'runtime/invalid'
    else:
        methods[1]['artifact_inputs']['manifest_directory'] = 'artifacts/blocked/manifests'
    # Real repair-stage methods are not run on the clear acceptance path.
    # The unchanged real integration selector selects both produced-result GREENs.
    tools, worktree, commit = integration_ready(project, methods)
    record = tools.runtime.task_queries.record('T1')
    root = Path(tools.runtime._roots(record)['task'])
    (root / 'artifacts').mkdir(exist_ok=True)
    if case == 'parent-file':
        (root / 'artifacts/blocked').write_bytes(b'preserve parent\n')
    elif case == 'symlink':
        (root / 'artifacts/real').mkdir()
        (root / 'artifacts/blocked').symlink_to(root / 'artifacts/real', target_is_directory=True)
    assert [m['id'] for m in tools.runtime.integration_tools.port._select_checks(record)] == [
        'INTEGRATION_0', 'INTEGRATION_1',
    ]
    assert all(not marker.exists() for marker in markers)
    sentinel = project['app'] / 'operator.txt'
    sentinel.write_bytes(b'preserve operator WIP\n')
    state = semantic_snapshot(tools.runtime)
    main = git_snapshot(project['app'])
    source = git_snapshot(worktree)
    files = node_snapshot(root)
    packet = integration_input(project, commit)
    try:
        with pytest.raises(PoiseError, match='[Mm]anifest|[Dd]irectory|[Ss]ymlink|[Aa]rtifact'):
            if entry == 'public':
                tools.invoke(request('integrate', packet))
            else:
                tools.runtime.integration_tools.apply(packet)
    finally:
        assert all(not marker.exists() for marker in markers), 'earlier consumer ran before later preflight'
        assert semantic_snapshot(tools.runtime) == state
        assert git_snapshot(project['app']) == main
        assert git_snapshot(worktree) == source
        assert node_snapshot(root) == files
        assert sentinel.read_bytes() == b'preserve operator WIP\n'
