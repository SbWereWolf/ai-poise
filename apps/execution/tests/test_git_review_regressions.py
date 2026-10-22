"""Independent real-Git regressions for T052 CODE-F01, F02 and F03."""
import json
from pathlib import Path
import re

import pytest

from apps.execution.shell import ExecutionError, StageCommitError
from git_diff_checks import (admission, arrange, bind, consumer, fixture as original_fixture,
                             git, packet, ref, saved, tree_files, working)
from process_checks import invoke
from store_checks import original_rows, snapshot


FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/git-review-regressions.json'


def fixture():
    return json.loads(FIXTURE.read_text())


def literal_tree(root, entries):
    """Construct expected objects from literals and actual module commit facts."""
    def build(items):
        children, rows = {}, []
        for path, entry in items.items():
            name, separator, tail = path.partition('/')
            if separator:
                children.setdefault(name, {})[tail] = entry
            else:
                oid = (git(root, 'hash-object', '-w', '--stdin',
                           data=entry['content'].encode()).decode().strip()
                       if entry['kind'] == 'blob' else entry['oid'])
                rows.append(f"{entry['mode']} {entry['kind']} {oid}\t{name}".encode() + b'\0')
        for name, child in children.items():
            rows.append(f'040000 tree {build(child)}\t{name}'.encode() + b'\0')
        return git(root, 'mktree', '-z', '--missing', data=b''.join(rows)).decode().strip()
    return build(entries)


def tree_entries(root, expression):
    entries = {}
    for row in git(root, 'ls-tree', '-rz', expression).split(b'\0'):
        if not row:
            continue
        header, path = row.split(b'\t', 1)
        mode, kind, oid = (part.decode() for part in header.split())
        assert kind in ('blob', 'commit')
        entry = {'mode': mode, 'kind': kind}
        if kind == 'blob':
            entry['content'] = git(root, 'cat-file', 'blob', oid).decode()
        else:
            entry['oid'] = oid
        entries[path.decode()] = entry
    return entries


def submodule_case(tmp_path, monkeypatch, kind):
    a = arrange(tmp_path, monkeypatch)
    root = a['root']
    module = root / 'extra/module'
    module.mkdir(parents=True)
    git(module, 'init', '-q')
    (module / 'nested.py').write_text(fixture()['module']['first'])
    git(module, 'add', '--all')
    git(module, 'commit', '-q', '-m', 'First module state')
    (root / 'src/result.py').write_text('A\n')
    (root / '.gitmodules').write_text(fixture()['module']['gitmodules'])
    git(root, 'add', '--all')
    git(root, 'commit', '-q', '-m', 'Parent module baseline')
    a['head'], a['entry'] = ref(root), ref(root, 'HEAD^{tree}')
    (module / 'nested.py').write_text(fixture()['module']['second'])
    git(module, 'add', '--all')
    git(module, 'commit', '-q', '-m', 'Second module state')
    module_head = ref(module)
    git(root, 'config', 'diff.ignoreSubmodules', 'all')
    expected = bind(fixture()['scenarios'][kind], {'$MODULE_HEAD': module_head})
    if kind == 'mixed':
        (root / 'src/result.py').write_text('C\n')
    a['candidate'] = literal_tree(root, expected['candidate'])
    assert tree_entries(root, a['candidate']) == expected['candidate']
    return a, module, expected


def assert_gitlink_admission(a, value, expected, before):
    assert value['root'] == {'kind': 'code', 'position': 0}
    assert value['role'] == 'author' and value['allowed'] is False
    assert value['baseline_tree'] == a['entry']
    assert value['head_commit'] == before['head']
    assert value['head_tree'] == before['head_tree']
    assert value['candidate_tree'] == a['candidate']
    assert value['stage_paths'] == value['commit_paths'] == expected['paths']
    assert value['decisions'] == expected['decisions']
    assert tree_entries(a['root'], value['candidate_tree']) == expected['candidate']


@pytest.mark.parametrize('kind', ['isolated', 'mixed'])
def test_gitlink_diff_ignores_display_configuration(tmp_path, monkeypatch, kind):
    a, module, expected = submodule_case(tmp_path, monkeypatch, kind)
    before = saved(a['root'])
    module_before = (ref(module), working(module))
    canonical = original_rows(a['database'])['executions']
    code, result = invoke(a['config'], packet(a))
    assert result['status'] == 'diff_checked', result
    assert code == 2 and result['allowed'] is False, result
    assert_gitlink_admission(a, result, expected, before)
    assert saved(a['root']) == before
    assert (ref(module), working(module)) == module_before
    assert original_rows(a['database'])['executions'] == canonical


@pytest.mark.parametrize('kind', ['isolated', 'mixed'])
def test_gitlink_commit_denial_preserves_work(tmp_path, monkeypatch, kind):
    a, module, expected = submodule_case(tmp_path, monkeypatch, kind)
    before, database = saved(a['root']), snapshot(a['database'])
    module_before = (ref(module), working(module))
    method, args = consumer(a)
    result = method(**args)
    assert result['status'] == 'commit_denied' and result['created'] is False, result
    assert result['commit'] == before['head'] and result['tree'] == before['head_tree']
    assert_gitlink_admission(a, result['admission'], expected, before)
    assert saved(a['root']) == before and snapshot(a['database']) == database
    assert (ref(module), working(module)) == module_before


def spaced_case(tmp_path, monkeypatch, case):
    a = arrange(tmp_path, monkeypatch, case)
    new_root = a['root'].with_name('code ')
    a['root'].rename(new_root)
    a['root'] = a['roots']['code'] = new_root
    declaration = bind(original_fixture()['declare'], {
        '@CONTRACT': a['contract_revision'], '$WORKSPACE_ID': 'workspace-0042',
        '$CODE_ROOT': str(new_root), '$MAIN_ROOT': str(a['roots']['main'])})
    context = fixture()['space_context']
    declaration['input'].update(context_id=context['id'], request_id=context['request_id'],
                                expected_revision=context['declaration_expected_revision'])
    code, result = invoke(a['config'], declaration)
    assert code == 0 and result['status'] == 'declared', result
    assert result['context_id'] == context['id'] and result['revision'] == 4
    assert git(new_root, 'rev-parse', '--show-toplevel') == str(new_root).encode() + b'\n'
    return a, context


@pytest.mark.parametrize('case', ['modify', 'rename-extra'])
def test_spaced_root_observation(tmp_path, monkeypatch, case):
    a, context = spaced_case(tmp_path, monkeypatch, case)
    before = saved(a['root'])
    value = packet(a, revision=context['query_expected_revision'])
    value['input']['context_id'] = context['id']
    code, result = invoke(a['config'], value)
    expected = a['case']['expected']['results'][0]
    assert result['status'] == 'diff_checked', result
    assert result['context_id'] == context['id'] and result['revision'] == 5
    assert code == expected['exit'] and result['allowed'] is expected['allowed']
    assert result['stage_id'] == 'build'
    assert result['stage_paths'] == a['case']['expected']['stage_paths']
    assert result['commit_paths'] == a['case']['expected']['commit_paths']
    assert result['decisions'] == expected['decisions']
    assert result['head_commit'] == before['head'] and result['head_tree'] == before['head_tree']
    assert result['baseline_tree'] == a['entry']
    assert tree_files(a['root'], result['candidate_tree']) == a['case']['expected']['candidate']
    assert saved(a['root']) == before


@pytest.mark.parametrize('case', ['modify', 'rename-extra'])
def test_spaced_root_commit(tmp_path, monkeypatch, case):
    a, context = spaced_case(tmp_path, monkeypatch, case)
    before, database = saved(a['root']), snapshot(a['database'])
    method, args = consumer(a, context_id=context['id'])
    result = method(**args)
    expected = a['case']['expected']['results'][0]
    assert result['status'] == ('committed' if expected['allowed'] else 'commit_denied')
    assert result['created'] is expected['allowed']
    admission(a, result['admission'], before=before)
    if expected['allowed']:
        assert result['commit'] == ref(a['root']) != before['head']
        assert result['tree'] == a['candidate']
        assert tree_files(a['root'], 'HEAD') == a['case']['expected']['candidate']
    else:
        assert saved(a['root']) == before
    assert working(a['root']) == before['working'] and snapshot(a['database']) == database


@pytest.mark.parametrize('item', fixture()['nul_inputs'], ids=lambda item: item['field'])
def test_unrepresentable_commit_input_precedes_staging(tmp_path, monkeypatch, item):
    a = arrange(tmp_path, monkeypatch, 'index-working')
    method, args = consumer(a, **{item['field']: item['value']})
    assert '\x00' in args[item['field']] and re.fullmatch(args['commit_pattern'], args['message'])
    before, database = saved(a['root']), snapshot(a['database'])
    with pytest.raises(ExecutionError) as error:
        method(**args)
    assert not isinstance(error.value, StageCommitError), error.value
    assert str(error.value)
    assert saved(a['root']) == before and snapshot(a['database']) == database
