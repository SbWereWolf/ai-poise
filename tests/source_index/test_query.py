"""Independent query input and complete saved machine-output consumers."""
import json

import pytest

from .helpers import FIXTURES, Scene, assert_failure, literal, phases


def test_saved_dirty_sources_are_updated_before_search(project, tmp_path):
    scene = Scene(project, tmp_path, real=True); scene.require()
    (scene.checkout / 'src/delivery.py').write_bytes((FIXTURES / 'delivery.py').read_bytes())
    scene.ready()
    from tests.conftest import git
    before = git(scene.checkout, 'rev-parse', 'HEAD')
    (scene.checkout / 'src/invoice.py').write_bytes((FIXTURES / 'dirty.py').read_bytes())
    (scene.checkout / 'src/delivery.py').write_bytes((FIXTURES / 'delivery-dirty.py').read_bytes())
    code, result = scene.invoke('query', ['refs', 'discount_total', '--format', 'json'])
    assert (code, result['status']) == (0, 'complete')
    assert git(scene.checkout, 'rev-parse', 'HEAD') == before
    from .test_actual import assert_definition, assert_import, assert_absent_refs
    assert_definition(scene.output(result), 'discount_total')
    assert_import(scene.output(result), 'discount_total')
    assert len(result['observations']) >= 2
    operations = [row['operation'] for row in result['observations']]
    assert operations.index('update') < operations.index('query')
    code, result = scene.invoke('query', ['refs', 'net_total', '--format', 'json'])
    assert code == 0
    assert_absent_refs(scene.output(result))


@pytest.mark.parametrize('case', ['nonzero', 'uninterpretable', 'missing-after-update'])
def test_update_failure_blocks_query(project, tmp_path, case):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    if case == 'nonzero':
        scene.controls['update_exit'] = 7
    else:
        scene.controls['after_update'] = case
    scene.save()
    code, result = scene.query()
    assert code == 1 and result['status'] in ('failed', 'unavailable'), result
    assert result['reason'] and result['recovery']
    assert 'update' in phases(scene)
    assert phases(scene).count('update') == 1
    assert 'usages' not in phases(scene)
    if case == 'nonzero':
        raw = scene.output(result, 'update')
        assert raw == 'UPDATE_STDOUT_SENTINEL\n'
        execution = next(row['execution'] for row in result['observations']
                         if row['operation'] == 'update')
        assert execution['actual_exit_code'] == 7 and execution['capture_complete'] is True
        from pathlib import Path
        assert Path(execution['stderr']).read_text() == 'UPDATE_STDERR_SENTINEL\n'


def test_query_failure_preserves_full_diagnostics(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    scene.controls['query_exit'] = 9; scene.save()
    code, result = scene.query()
    assert_failure(code, result, 'failed', 'query')
    assert json.loads(scene.output(result)) == literal('query.json')
    execution = next(row['execution'] for row in result['observations']
                     if row['operation'] == 'query')
    assert execution['actual_exit_code'] == 9 and execution['capture_complete'] is True
    from pathlib import Path
    assert Path(execution['stderr']).read_text() == 'QUERY_STDERR_TAIL\n'
    assert phases(scene).count('usages') == 1


def test_complete_machine_output_is_read_beyond_preview(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    scene.controls['large'] = True; scene.save()
    code, result = scene.query()
    assert (code, result['status']) == (0, 'complete')
    raw = scene.output(result)
    assert len(raw) > project['cfg']['limits']['preview_chars']
    assert raw == (FIXTURES / 'large-query.json').read_text()
    complete = json.loads(raw)
    assert complete == literal('large-query.json')
    stats = next(row['execution'] for row in result['observations'] if row['operation'] == 'stats')
    from pathlib import Path
    full_stats = Path(stats['stdout']).read_text()
    assert len(full_stats) > project['cfg']['limits']['preview_chars']
    assert json.loads(full_stats)['stats']['file_count'] == 1
