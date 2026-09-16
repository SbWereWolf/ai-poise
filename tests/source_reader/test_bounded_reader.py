import json
from pathlib import Path
import subprocess
import sys

import pytest

from poise.common import PoiseError
from poise.infrastructure.source_reader import FileSourceReader

POLICY = dict(schema='source-reader-1', max_items=3, max_lines=8,
              max_file_bytes=1024, max_output_bytes=64, max_receipts=3,
              lock_wait_seconds=1, lock_poll_seconds=0.01)


def reader(tmp_path, **limits):
    return FileSourceReader(tmp_path / 'state.json', {**POLICY, **limits})


def request(r, tmp_path, path='one.txt', start=1, end=2, **extra):
    return dict(cwd=str(tmp_path), generation=r.context()['generation'],
                acknowledged=[], reads=[dict(path=path,start=start,end=end,
                reread=False,reason='')], **extra)


def test_exact_range_acknowledgement_and_repeat(tmp_path):
    (tmp_path/'one.txt').write_text('one\ntwo\nthree\n')
    r=reader(tmp_path); q=request(r,tmp_path)
    items=r.read(q)['items'];assert len(items)==1, 'Every requested range must produce a result'
    first=items[0]
    assert first['text']=='one\ntwo\n'
    assert first['status']=='read'
    assert r.read(q)['items'][0]['status']=='read', 'Unacknowledged output is not model memory'
    q['acknowledged']=[first['receipt_id']]
    second=r.read(q)['items'][0]
    assert second['status']=='unchanged'
    assert 'text' not in second
    state=(tmp_path/'state.json').read_text()
    assert 'one\\ntwo' not in state, 'Receipts are metadata, not a document cache'


def test_content_range_and_generation_are_all_part_of_identity(tmp_path):
    p=tmp_path/'one.txt';p.write_text('one\ntwo\nthree\n')
    r=reader(tmp_path);q=request(r,tmp_path)
    a=r.read(q)['items'][0];q['acknowledged']=[a['receipt_id']];r.read(q)
    q['reads'][0]['start']=2
    assert r.read(q)['items'][0]['text']=='two\n'
    q['reads'][0]['start']=1;p.write_text('new\ntwo\nthree\n')
    assert r.read(q)['items'][0]['text']=='new\ntwo\n'
    old=q['generation'];new=r.reset('compact','event-2')['generation']
    assert new>old
    with pytest.raises(PoiseError,match='generation'):r.read(q)
    q['generation']=new;q['acknowledged']=[]
    assert r.read(q)['items'][0]['status']=='read'
    assert r.reset('compact','event-2')['generation']==new


def test_force_reread_is_auditable_and_needs_reason(tmp_path):
    (tmp_path/'one.txt').write_text('a\nb\n');r=reader(tmp_path);q=request(r,tmp_path)
    a=r.read(q)['items'][0];q['acknowledged']=[a['receipt_id']]
    q['reads'][0].update(reread=True,reason='Verify a disputed line')
    got=r.read(q)['items'][0]
    assert (got['status'],got['text'],got['reason'])==('reread','a\nb\n','Verify a disputed line')
    assert json.loads((tmp_path/'state.json').read_text())['receipts'][-1]['reason']=='Verify a disputed line'
    q['reads'][0]['reason']=''
    with pytest.raises(PoiseError,match='reason'):r.read(q)


@pytest.mark.parametrize('mutator',[
    lambda q:q['reads'].__imul__(4),
    lambda q:q['reads'][0].update(start=0),
    lambda q:q['reads'][0].update(end=10),
    lambda q:q['reads'][0].update(start=True),
    lambda q:q.update(generation=True),
    lambda q:q.update(acknowledged=['invented']),
])
def test_invalid_batch_has_no_receipts(tmp_path,mutator):
    (tmp_path/'one.txt').write_text('a\nb\n');r=reader(tmp_path);q=request(r,tmp_path)
    before=(tmp_path/'state.json').read_bytes();mutator(q)
    with pytest.raises(PoiseError):r.read(q)
    assert (tmp_path/'state.json').read_bytes()==before


def test_explicit_absolute_relative_paths_bounded_output_and_fail_atomicity(tmp_path):
    (tmp_path/'one.txt').write_text('a\nb\n');r=reader(tmp_path);q=request(r,tmp_path)
    q['reads'].append(dict(q['reads'][0],path=str(tmp_path/'one.txt')))
    got=r.read(q)
    assert [i['text'] for i in got['items']]==['a\nb\n','a\nb\n']
    before=(tmp_path/'state.json').read_bytes()
    q['reads'].append(dict(q['reads'][0],path='missing'))
    with pytest.raises(PoiseError):r.read(q)
    assert (tmp_path/'state.json').read_bytes()==before
    (tmp_path/'one.txt').write_text('x'*65+'\ny\n');q['reads']=q['reads'][:1]
    with pytest.raises(PoiseError,match='output'):r.read(q)


def test_large_binary_directory_and_fifo_rejected(tmp_path):
    import os
    r=reader(tmp_path)
    for name in ('large','binary','directory','fifo'):
        p=tmp_path/name
        if name=='large':p.write_bytes(b'a'*1025)
        elif name=='binary':p.write_bytes(b'\x00abc')
        elif name=='directory':p.mkdir()
        else:os.mkfifo(p)
        with pytest.raises(PoiseError):r.read(request(r,tmp_path,name))


def test_receipts_persist_but_are_bounded_and_only_metadata(tmp_path):
    (tmp_path/'one.txt').write_text('1\n2\n3\n4\n');r=reader(tmp_path)
    for line in range(1,5):r.read(request(r,tmp_path,start=line,end=line))
    raw=json.loads((tmp_path/'state.json').read_text())
    assert len(raw['receipts'])==3
    new=reader(tmp_path);q=request(new,tmp_path,start=4,end=4)
    q['acknowledged']=[raw['receipts'][-1]['receipt_id']]
    assert new.read(q)['items'][0]['status']=='unchanged'


def test_corrupt_state_is_not_silently_reset(tmp_path):
    r=reader(tmp_path);r.context();(tmp_path/'state.json').write_text('{broken')
    with pytest.raises(PoiseError):r.context()


def test_read_cli_from_arbitrary_cwd(tmp_path):
    import os
    repo=Path(__file__).resolve().parents[2]
    policy=tmp_path/'policy.json';policy.write_text(json.dumps(POLICY))
    p=subprocess.run([sys.executable,str(repo/'tools/source_read.py'),
                      '--state',str(tmp_path/'state.json'),'--policy',str(policy)],
                     input=json.dumps({'operation':'context','input':{}}),text=True,
                     capture_output=True,cwd=tmp_path,
                     env={**os.environ,'PYTHONPATH':str(repo/'src')},timeout=10)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)['generation']>=1
