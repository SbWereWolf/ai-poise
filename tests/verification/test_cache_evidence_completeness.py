"""Cache reuse proves the present bundle; it never reruns to hide lost evidence."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from poise.common import PoiseError
from poise.infrastructure.test_package_cache import AiPoiseTestPackageCache
from verification.test_ai_poise_test_package_cache import checkout


OUTPUTS = {'stdout': b'out\n', 'stderr': b'err\n',
           'junit': b'<testsuite><testcase name="sample"/></testsuite>'}


class SavedRunner:
    def __init__(self, changes=None, junit=None):
        self.calls = []
        self.changes = changes or {}
        self.junit = junit

    def run(self, run_id, command, cwd, env, timeout, stdout, stderr):
        self.calls.append(run_id)
        stdout.write_bytes(OUTPUTS['stdout'])
        stderr.write_bytes(OUTPUTS['stderr'])
        Path(command[command.index('--junitxml')+1]).write_bytes(self.junit if self.junit is not None else OUTPUTS['junit'])
        return {'actual_exit_code': 0, 'timed_out': False, 'cancelled': False,
                'duration_seconds': 0.01, 'capture_complete': True, **self.changes}


class ForbiddenRunner:
    def run(self, *args, **kwargs):
        pytest.fail('Reuse/corruption detection must not execute another runner')


def sample(tmp_path):
    root, catalog = checkout(tmp_path)
    runner = SavedRunner()
    cache = AiPoiseTestPackageCache(root, catalog, runner)
    first = cache.run('sample', timeout_seconds=2)
    assert first['passed'] is True
    cache.runner = ForbiddenRunner()
    return root, cache, first


def result_path(root, first):
    record = json.loads((root / first['cache_record']).read_text())
    return root / record['evidence_path']


def edit_evidence(root, first, edit):
    path = result_path(root, first)
    value = json.loads(path.read_text())
    edit(value)
    path.write_text(json.dumps(value))
    record_path = root / first['cache_record']
    record = json.loads(record_path.read_text())
    record['evidence_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    record_path.write_text(json.dumps(record))


@pytest.mark.parametrize('name', ['stdout', 'stderr', 'junit', 'result'])
@pytest.mark.parametrize('damage', ['missing', 'same_size_mutation', 'symlink', 'directory'])
def test_reuse_rejects_unavailable_or_changed_primary_files(tmp_path, name, damage):
    root, cache, first = sample(tmp_path)
    path = result_path(root, first) if name == 'result' else root / first[name]
    before_record = (root / first['cache_record']).read_bytes()
    original = path.read_bytes()
    path.unlink()
    if damage == 'same_size_mutation':
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    elif damage == 'symlink':
        target = root / 'other-evidence'; target.write_bytes(original); path.symlink_to(target)
    elif damage == 'directory':
        path.mkdir()
    with pytest.raises(PoiseError, match='--fresh'):
        cache.run('sample', timeout_seconds=2)
    assert (root / first['cache_record']).read_bytes() == before_record


def test_fresh_manifest_matches_real_files_and_hit_relocates(tmp_path):
    root, cache, first = sample(tmp_path)
    manifest = first['primary_evidence']
    assert manifest['schema'] == 'ai-poise-primary-evidence-1'
    assert set(manifest['files']) == {'stdout','stderr','junit'}
    for name, content in OUTPUTS.items():
        assert manifest['files'][name] == {'path':first[name], 'bytes':len(content),
                                           'sha256':hashlib.sha256(content).hexdigest()}
    moved = tmp_path / 'moved'; shutil.copytree(root,moved)
    other = AiPoiseTestPackageCache(moved,moved/'config/testing/test-packages.json',ForbiddenRunner())
    hit = other.run('sample',timeout_seconds=0.01)
    assert hit['passed'] and hit['reused'] and not hit['executed']
    assert hit['primary_evidence'] == manifest
    assert hit['origin_run_id'] == first['origin_run_id']
    assert hit['input_fingerprint'] == first['input_fingerprint']


@pytest.mark.parametrize('bad', ['absent','schema','missing_role','extra_role','shape','size','boolean_size','digest','path','incomplete','bad_result'])
def test_invalid_manifest_or_result_cannot_hide_behind_valid_result_digest(tmp_path,bad):
    root, cache, first = sample(tmp_path)
    def edit(value):
        if bad=='bad_result':
            value['actual_exit_code']=1; return
        if bad=='incomplete':
            value['capture_complete']=False; return
        manifest=value.get('primary_evidence', {'schema':'ai-poise-primary-evidence-1','files':{
            name:{'path':first[name],'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
            for name,content in OUTPUTS.items()}})
        value['primary_evidence']=manifest
        if bad=='absent': value.pop('primary_evidence')
        elif bad=='schema': manifest['schema']='unknown'
        elif bad=='missing_role': manifest['files'].pop('junit')
        elif bad=='extra_role': manifest['files']['other']=manifest['files']['stdout']
        elif bad=='shape': manifest['files']['stdout']=[]
        elif bad=='size': manifest['files']['stdout']['bytes']=100
        elif bad=='boolean_size': manifest['files']['stdout']['bytes']=False
        elif bad=='digest': manifest['files']['stdout']['sha256']='0'*64
        elif bad=='path': manifest['files']['stdout']['path']='src/pkg/value.py'
    edit_evidence(root,first,edit)
    with pytest.raises(PoiseError,match='--fresh'): cache.run('sample',timeout_seconds=2)


@pytest.mark.parametrize('field,value', [('schema','ai-poise-test-package-cache-1'),('origin_run_id','../foreign'),
    ('package_id','other'),('input_fingerprint','0'*64),('evidence_path','src/pkg/value.py'),('evidence_path','/tmp/external')])
def test_invalid_or_legacy_record_requires_explicit_fresh(tmp_path,field,value):
    root,cache,first=sample(tmp_path)
    path=root/first['cache_record']; record=json.loads(path.read_text());record[field]=value;path.write_text(json.dumps(record))
    with pytest.raises(PoiseError,match='--fresh'):cache.run('sample',timeout_seconds=2)


@pytest.mark.parametrize('component', ['.poise-test-cache','.poise-test-cache/evidence','.poise-test-cache/records'])
def test_symlinked_cache_parent_is_not_followed(tmp_path,component):
    root,cache,first=sample(tmp_path)
    path=root/component; other=tmp_path/'aliased';path.rename(other);path.symlink_to(other,target_is_directory=True)
    with pytest.raises(PoiseError,match='--fresh'):cache.run('sample',timeout_seconds=2)


def test_explicit_fresh_recovers_but_preserves_prior_history(tmp_path):
    root,cache,first=sample(tmp_path)
    old_result=result_path(root,first); old_bytes=old_result.read_bytes()
    (root/first['stdout']).unlink()
    with pytest.raises(PoiseError,match='--fresh'):cache.run('sample',timeout_seconds=2)
    runner=SavedRunner();cache.runner=runner
    second=cache.run('sample',timeout_seconds=2,fresh=True)
    assert len(runner.calls)==1
    assert second['origin_run_id']!=first['origin_run_id']
    assert second['input_fingerprint']==first['input_fingerprint']
    assert second['executed'] and not second['reused']
    assert old_result.read_bytes()==old_bytes
    cache.runner=ForbiddenRunner()
    assert cache.run('sample',timeout_seconds=2)['origin_run_id']==second['origin_run_id']


@pytest.mark.parametrize('changes,junit', [({'actual_exit_code':1},None),({'timed_out':True},None),
    ({'cancelled':True},None),({'capture_complete':False},None),({},b'<testsuite><testcase name="bad"><failure/></testcase></testsuite>')])
def test_failed_or_incomplete_run_does_not_publish_reusable_record(tmp_path,changes,junit):
    root,catalog=checkout(tmp_path);runner=SavedRunner(changes,junit)
    cache=AiPoiseTestPackageCache(root,catalog,runner)
    result=cache.run('sample',timeout_seconds=2)
    assert not result['passed']
    assert result['cache_record'] is None
    assert not list((root/'.poise-test-cache/records').rglob('*.json'))
    assert len(runner.calls)==1


def test_disappearance_during_file_open_is_controlled(tmp_path,monkeypatch):
    root,cache,first=sample(tmp_path)
    target=root/first['stdout'];original=Path.open
    def disappearing(path,*args,**kwargs):
        if path==target:
            path.unlink(missing_ok=True)
            raise FileNotFoundError(str(path))
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',disappearing)
    with pytest.raises(PoiseError,match='--fresh'):cache.run('sample',timeout_seconds=2)


def test_malformed_json_result_with_matching_digest_is_controlled(tmp_path):
    root,cache,first=sample(tmp_path)
    path=result_path(root,first);path.write_text('{')
    record_path=root/first['cache_record']; record=json.loads(record_path.read_text())
    record['evidence_sha256']=hashlib.sha256(path.read_bytes()).hexdigest();record_path.write_text(json.dumps(record))
    with pytest.raises(PoiseError,match='--fresh'):cache.run('sample',timeout_seconds=2)


def test_empty_streams_remain_required_and_valid(tmp_path,monkeypatch):
    monkeypatch.setitem(OUTPUTS,'stdout',b'')
    monkeypatch.setitem(OUTPUTS,'stderr',b'')
    root,cache,first=sample(tmp_path)
    assert first['primary_evidence']['files']['stdout']['bytes']==0
    assert first['primary_evidence']['files']['stderr']['sha256']==hashlib.sha256(b'').hexdigest()
    assert cache.run('sample',timeout_seconds=2)['origin_run_id']==first['origin_run_id']


def test_absent_record_is_a_miss_without_deleting_old_origin(tmp_path):
    root,cache,first=sample(tmp_path)
    old=result_path(root,first); old_bytes=old.read_bytes()
    (root/first['cache_record']).unlink()
    runner=SavedRunner();cache.runner=runner
    new=cache.run('sample',timeout_seconds=2)
    assert len(runner.calls)==1 and new['executed']
    assert new['origin_run_id']!=first['origin_run_id']
    assert old.read_bytes()==old_bytes


def test_broken_record_symlink_is_not_a_miss(tmp_path):
    root,cache,first=sample(tmp_path)
    path=root/first['cache_record'];path.unlink();path.symlink_to(root/'does-not-exist')
    with pytest.raises(PoiseError,match='--fresh'):cache.run('sample',timeout_seconds=2)


def test_fresh_does_not_follow_symlinked_storage(tmp_path):
    root,cache,first=sample(tmp_path)
    path=root/'.poise-test-cache';alias=tmp_path/'alias';path.rename(alias);path.symlink_to(alias,target_is_directory=True)
    with pytest.raises(PoiseError,match='regular'):cache.run('sample',timeout_seconds=2,fresh=True)
