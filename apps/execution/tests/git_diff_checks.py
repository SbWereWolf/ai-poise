"""Literal Git fixtures and independent plumbing; no production oracle."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from process_checks import installation, create, invoke, write_json, store
from store_checks import original_rows, snapshot, stored_records

APP = Path(__file__).resolve().parents[1]
REAL_GIT = shutil.which('git')


def fixture():
    return json.loads((APP / 'fixtures/git-diff-contract.json').read_text())


def case(name):
    return next(c for c in fixture()['cases'] if c['id'] == name)


def git(root, *args, data=None):
    result = subprocess.run([REAL_GIT, '-C', str(root), *args], input=data,
                            capture_output=True)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout


def ref(root, value='HEAD'):
    return git(root, 'rev-parse', value).decode().strip()


def tree(root, files):
    """Build an expected tree from literal maps, without index/snapshot calls."""
    def build(items):
        children, entries = {}, []
        for path, value in items.items():
            first, sep, rest = path.partition('/')
            if sep:
                children.setdefault(first, {})[rest] = value
            else:
                blob = git(root, 'hash-object', '-w', '--stdin',
                           data=value['content'].encode()).decode().strip()
                entries.append(f"{value['mode']} blob {blob}\t{first}".encode() + b'\0')
        for name, values in children.items():
            entries.append(f'040000 tree {build(values)}\t{name}'.encode() + b'\0')
        return git(root, 'mktree', '-z', data=b''.join(entries)).decode().strip()
    return build(files)


def tree_files(root, value):
    result = {}
    for row in git(root, 'ls-tree', '-rz', value).split(b'\0'):
        if not row:
            continue
        meta, name = row.split(b'\t', 1)
        mode, kind, oid = meta.split()
        assert kind == b'blob'
        result[name.decode()] = {'mode': mode.decode(),
                                'content': git(root, 'cat-file', 'blob', oid.decode()).decode()}
    return result


def working(root):
    return {p.relative_to(root).as_posix(): {'content': p.read_text(),
            'mode': '100755' if p.stat().st_mode & 0o111 else '100644'}
            for p in root.rglob('*') if '.git' not in p.relative_to(root).parts and p.is_file()}


def put_working(root, files):
    for p in sorted(root.rglob('*'), reverse=True):
        if '.git' not in p.relative_to(root).parts and p.is_file():
            p.unlink()
    for name, value in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value['content'])
        p.chmod(0o755 if value['mode'] == '100755' else 0o644)


def saved(root):
    return {'head': ref(root), 'index': (root / '.git/index').read_bytes(),
            'working': working(root)}


def bind(value, mapping):
    if type(value) is dict:
        return {k: bind(v, mapping) for k, v in value.items()}
    if type(value) is list:
        return [bind(v, mapping) for v in value]
    return mapping.get(value, value) if type(value) is str else value


def arrange(tmp_path, monkeypatch, name='modify'):
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', '/dev/null')
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', '/dev/null')
    monkeypatch.delenv('GIT_INDEX_FILE', raising=False)
    for key, val in {'GIT_AUTHOR_NAME':'Arrangement','GIT_AUTHOR_EMAIL':'arrange@example.test',
                     'GIT_COMMITTER_NAME':'Arrangement','GIT_COMMITTER_EMAIL':'arrange@example.test'}.items():
        monkeypatch.setenv(key, val)
    c = case(name)
    config = installation(tmp_path)
    mapping = {'@PROCESS': None, '@CONTRACT': None, '$WORKSPACE_ID':'workspace-0042'}
    roots = {}
    for kind in ['code', 'main']:
        root = tmp_path / kind
        root.mkdir()
        git(root, 'init', '-q')
        roots[kind] = root
        mapping['$CODE_ROOT' if kind == 'code' else '$MAIN_ROOT'] = str(root)
        et = tree(root, c['given']['entry'])
        hc_tree = tree(root, c['given']['head'])
        ec = git(root, 'commit-tree', et, '-m', 'Entry').decode().strip()
        hc = git(root, 'commit-tree', hc_tree, '-p', ec, '-m', 'Base').decode().strip()
        git(root, 'update-ref', 'HEAD', hc)
        git(root, 'read-tree', tree(root, c['given']['index']))
        put_working(root, c['given']['working'])
    source = fixture()
    code, published = invoke(config, create(definition=source['definition']))
    assert code == 0 and published['status'] == 'published', published
    mapping['@PROCESS'] = published['process_revision']
    contract = bind(source['contract'], mapping)
    packet = {'schema':'stride-execution/v1','operation':'apply','input':{
        'request_id':'setup-git','expected_revision':1,'operations':[
            {'id':'setup','kind':'setup_execution','contract':contract}]}}
    code, applied = invoke(config, packet)
    assert code == 0 and applied['status'] == 'applied', applied
    mapping['@CONTRACT'] = applied['results'][0]['contract_revision']
    declaration = bind(source['declare'], mapping)
    data = json.loads(config.read_text())
    data['git'] = {'seconds':10.0,'observations':'git-observations'}
    data['gates'] = {'roots':{key:str(tmp_path/key) for key in ['runtime','task','sprint']}}
    write_json(config, data)
    code, declared = invoke(config, declaration)
    assert code == 0 and declared['status'] == 'declared', declared
    root = roots[c['root']['kind']]
    other = tmp_path / 'other-context'
    other.mkdir()
    put_working(other, c['given'].get('other_context', {}))
    return {'config':config,'case':c,'root':root,'roots':roots,'other':other,
            'entry':tree(root,c['given']['entry']),'head':ref(root),
            'candidate':tree(root,c['expected']['candidate']),
            'database':tmp_path/'state/execution.sqlite',
            'process_revision':mapping['@PROCESS'],'contract_revision':mapping['@CONTRACT']}


def packet(a, *, stage=None, request='diff-1', revision=3):
    value = fixture()['diff_packet']
    value['input'].update(stage_id=stage or a['case']['stages'][0], request_id=request,
        expected_revision=revision, root=a['case']['root'], baseline_ref=a['entry'])
    return value


def observed(a, value=None, expected=None):
    value = packet(a) if value is None else value
    code, result = invoke(a['config'], value)
    assert result['status'] == 'diff_checked', result  # Missing capability is behavioral RED.
    expected = a['case']['expected']['results'][0] if expected is None else expected
    assert code == expected['exit'], result
    assert set(result) == {'schema','status','revision','replayed','task_id','generation',
        'context_id','stage_id','root','role','baseline_tree','head_commit','head_tree',
        'candidate_tree','stage_paths','commit_paths','decisions','allowed','result_id'}
    assert result['revision'] == value['input']['expected_revision'] + 1
    assert result['replayed'] is False and result['task_id'] == '0042'
    assert result['generation'] == 1 and result['context_id'] == 'scope-1'
    for field in ['stage_id','role','allowed','decisions']:
        assert result[field] == expected[field]
    assert result['root'] == a['case']['root']
    assert result['baseline_tree'] == a['entry']
    assert result['head_commit'] == ref(a['root'])
    assert result['head_tree'] == ref(a['root'],'HEAD^{tree}')
    assert tree_files(a['root'], result['candidate_tree']) == a['case']['expected']['candidate']
    for field in ['stage_paths','commit_paths']:
        assert result[field] == a['case']['expected'][field]
    assert type(result['result_id']) is str and result['result_id']
    return result


def query_packet(request='diff-1'):
    value = fixture()['query_packet']
    value['input']['queries'][0]['request_id'] = request
    return value


def consumer(a, **changes):
    from apps.execution.shell import Store, load_config
    owner = Store(load_config(a['config']))
    method = getattr(owner, 'prepare_stage_commit', None)
    assert callable(method), 'Missing public Store.prepare_stage_commit behavior'
    args = fixture()['consumer_args']
    args.update(stage_id=a['case']['stages'][0], root=a['case']['root'],
                baseline_ref=a['entry'], expected_head=a['head'], candidate_tree=a['candidate'])
    args.update(changes)
    return method, args


def admission(a, result, expected=None):
    assert set(result) == {'root','role','baseline_tree','head_commit','head_tree',
                          'candidate_tree','stage_paths','commit_paths','decisions','allowed'}
    expected = a['case']['expected']['results'][0] if expected is None else expected
    for key in ['root']:
        assert result[key] == a['case'][key]
    for key in ['role','decisions','allowed']:
        assert result[key] == expected[key]
    for key in ['stage_paths','commit_paths']:
        assert result[key] == a['case']['expected'][key]
    assert result['baseline_tree'] == a['entry']
    assert result['candidate_tree'] == a['candidate']


def git_wrapper(a, monkeypatch, mode):
    """Delegate real commands; phase faults never calculate expected decisions."""
    folder = a['config'].parent/'git-wrapper'
    folder.mkdir()
    path = folder/'git'
    script = fixture()['git_wrapper'].replace('@PYTHON@', sys.executable)
    path.write_text(script)
    path.chmod(0o755)
    monkeypatch.setenv('PATH',str(folder)+os.pathsep+os.environ['PATH'])
    monkeypatch.setenv('T052_GIT_MODE',mode)
    monkeypatch.setenv('T052_GIT_MARKER',str(folder/'observed'))
    monkeypatch.setenv('T052_REAL_GIT',REAL_GIT)
    return folder
