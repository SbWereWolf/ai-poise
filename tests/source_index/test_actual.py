"""Real third-party fixture control and product end-to-end source relations."""
import json
from pathlib import Path
import subprocess

from tests.conftest import git, write_json
from .helpers import FIXTURES, Scene


def external(scene, operation, *args, checkout=None):
    checkout = checkout or scene.checkout
    env = dict(scene.environment, AST_INDEX_ROOT=str(checkout),
               AST_INDEX_DB_PATH=str(checkout / '.runtime/ast-index/index.db'))
    argv = [str(scene.external), operation, *args, '--format', 'json']
    cp = subprocess.run(argv, cwd=checkout, env=env, text=True, capture_output=True)
    scene.calls.append({'argv': argv, 'cwd': str(checkout), 'exit_code': cp.returncode,
                        'stdout': cp.stdout, 'stderr': cp.stderr})
    write_json(scene.parent / 'external-calls.json', scene.calls)
    assert cp.returncode == 0, scene.calls[-1]
    return cp.stdout


def assert_definition(raw, name):
    value = json.loads(raw)
    symbols = value['symbols'] if 'symbols' in value else value['definitions']
    # Assert the actual indexed semantic fields, not a substring of JSON text.
    assert any(row['kind'] == 'function' and row['name'] == name
               and row['path'] == 'src/invoice.py' and row['line'] == 1
               and row['signature'] == 'def ' + name + '(amount):'
               for row in symbols), raw


def assert_callsite(raw, name):
    value = json.loads(raw)
    assert any(row['path'] == 'src/invoice.py' and row['line'] == 5
               and row['content'] == 'return ' + name + '(amount)'
               for row in value['content_matches']), raw


def assert_import(raw, name):
    value = json.loads(raw)
    assert any(row['kind'] == 'import' and row['name'] == name
               and row['path'] == 'src/delivery.py' and row['line'] == 1
               and row['signature'] == 'from invoice import ' + name
               for row in value['imports']), raw


def assert_absent_refs(raw):
    value = json.loads(raw)
    assert value['definitions'] == []
    assert value['imports'] == []


def test_external_fixture_proves_expected_relationships(project, tmp_path):
    scene = Scene(project, tmp_path, real=True)
    assert scene.external.is_file()
    version = subprocess.run([str(scene.external), '--version'], text=True, capture_output=True)
    assert version.returncode == 0 and '3.50.0' in version.stdout
    external(scene, 'rebuild')
    external(scene, 'update')
    raw = external(scene, 'search', 'net_total')
    assert_definition(raw, 'net_total'); assert_callsite(raw, 'net_total')
    # This is an observed third-party limitation, not positive call-graph proof.
    assert json.loads(external(scene, 'usages', 'net_total')) == []
    (scene.checkout / 'src/delivery.py').write_bytes((FIXTURES / 'delivery.py').read_bytes())
    external(scene, 'update')
    raw = external(scene, 'refs', 'net_total')
    assert_definition(raw, 'net_total'); assert_import(raw, 'net_total')
    (scene.checkout / 'src/invoice.py').write_bytes((FIXTURES / 'dirty.py').read_bytes())
    (scene.checkout / 'src/delivery.py').write_bytes((FIXTURES / 'delivery-dirty.py').read_bytes())
    # content_matches may already reflect disk: indexed symbols/imports must not.
    before = external(scene, 'search', 'discount_total')
    assert json.loads(before)['symbols'] == []
    assert_absent_refs(external(scene, 'refs', 'discount_total'))
    external(scene, 'update')
    after = external(scene, 'search', 'discount_total')
    assert_definition(after, 'discount_total'); assert_callsite(after, 'discount_total')
    refs = external(scene, 'refs', 'discount_total')
    assert_definition(refs, 'discount_total'); assert_import(refs, 'discount_total')
    assert_absent_refs(external(scene, 'refs', 'net_total'))
    sibling = scene.sibling()
    external(scene, 'rebuild', checkout=sibling)
    beta = external(scene, 'refs', 'gross_total', checkout=sibling)
    assert_definition(beta, 'gross_total'); assert_import(beta, 'gross_total')
    assert_absent_refs(external(scene, 'refs', 'net_total', checkout=sibling))
    missing = json.loads(external(scene, 'search', 'net_total', checkout=sibling))
    assert missing == {key: [] for key in ('content_matches', 'files', 'references', 'symbols')}


def test_real_index_wrapper_tracks_saved_and_committed_sources(project, tmp_path):
    scene = Scene(project, tmp_path, real=True); scene.require(); scene.ready()
    code, result = scene.invoke('query', ['search', 'net_total', '--format', 'json'])
    assert (code, result['status']) == (0, 'complete')
    assert_definition(scene.output(result), 'net_total')
    assert_callsite(scene.output(result), 'net_total')
    scene.hooks()
    (scene.checkout / 'src/delivery.py').write_bytes((FIXTURES / 'delivery.py').read_bytes())
    git(scene.checkout, 'add', 'src/delivery.py')
    git(scene.checkout, 'commit', '-m', 'Shipping references the invoice total')
    head = git(scene.checkout, 'rev-parse', 'HEAD')
    trace = [json.loads(line) for line in scene.hook_trace.read_text().splitlines()]
    assert any(row['position'] == 'index' and row['head'] == head and row['exit_code'] == 0
               for row in trace)
    code, result = scene.invoke('query', ['refs', 'net_total', '--format', 'json'])
    assert (code, result['status']) == (0, 'complete')
    assert_definition(scene.output(result), 'net_total'); assert_import(scene.output(result), 'net_total')
    (scene.checkout / 'src/invoice.py').write_bytes((FIXTURES / 'dirty.py').read_bytes())
    (scene.checkout / 'src/delivery.py').write_bytes((FIXTURES / 'delivery-dirty.py').read_bytes())
    before = git(scene.checkout, 'rev-parse', 'HEAD')
    code, result = scene.invoke('query', ['refs', 'discount_total', '--format', 'json'])
    assert code == 0
    assert_definition(scene.output(result), 'discount_total'); assert_import(scene.output(result), 'discount_total')
    assert git(scene.checkout, 'rev-parse', 'HEAD') == before
    code, result = scene.invoke('query', ['refs', 'net_total', '--format', 'json'])
    assert code == 0; assert_absent_refs(scene.output(result))
    sibling = scene.sibling()
    code, result = scene.invoke('rebuild', checkout=sibling)
    assert code == 0
    code, result = scene.invoke('query', ['refs', 'gross_total', '--format', 'json'], checkout=sibling)
    assert code == 0 and result['database'] == str(sibling / '.runtime/ast-index/index.db')
    assert_definition(scene.output(result), 'gross_total'); assert_import(scene.output(result), 'gross_total')
