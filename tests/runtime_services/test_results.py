"""Real streams and subprocess workers; formatter health never rewrites a verdict."""
import json
from copy import deepcopy
from pathlib import Path
import pytest
from poise.common import PoiseError, file_digest
from poise.infrastructure.result_views import ResultViews
from poise.modules.result_views.domain import OutputPolicy

ROOT=Path(__file__).resolve().parents[2]

def config():
    return json.loads((ROOT/'config/runtime.example.json').read_text())['output']

def receipt(tmp_path, out=b'hello\n', err=b'Ran 2 tests in 0.01s\n\nOK\n'):
    tmp_path.mkdir(parents=True, exist_ok=True)
    a=tmp_path/'stdout.txt'; b=tmp_path/'stderr.txt';a.write_bytes(out);b.write_bytes(err)
    return {'id':tmp_path.name,'stdout':str(a),'stderr':str(b),
            'stdout_digest':file_digest(a),'stderr_digest':file_digest(b),
            'argv':['python','-m','unittest'],'actual_exit_code':0,'timed_out':False,
            'duration_seconds':0.1,'passed':True,'method':'TEST'}


def test_primary_is_bounded_and_all_named_files_persist(tmp_path):
    r=receipt(tmp_path/'run',b'x'*15000+b'\n')
    journal=[];svc=ResultViews(config(),lambda name,data:journal.append((name,data)))
    view=svc.capture(r,tmp_path/'run')
    assert len(view['primary'])<=config()['known'][0]['primary_chars']
    assert svc.finish()==[]
    manifest=json.loads(Path(view['manifest']).read_text())
    assert manifest['status']=='ready'
    assert set(manifest['representations'])=={'extended','full','selected'}
    full=Path(manifest['representations']['full']['path']).read_bytes()
    assert b'x'*15000 in full and b'Ran 2 tests' in full
    assert manifest['representations']['full']['bytes']==len(full)
    assert not any(n.startswith('incident') for n,d in journal)


def test_unknown_command_uses_explicit_profile_with_two_views(tmp_path):
    r=receipt(tmp_path/'run');r['argv']=['mystery-command']
    svc=ResultViews(config(),lambda *_:None); view=svc.capture(r,tmp_path/'run');svc.finish()
    m=json.loads(Path(view['manifest']).read_text())
    assert m['profile']=='unknown'
    assert set(m['representations'])=={'extended','full'}


def test_materialization_and_primary_use_same_parser(tmp_path):
    from poise.infrastructure.result_views import OutputParser
    r=receipt(tmp_path/'run');p=OutputPolicy.parse(config()).select(r['argv'])
    parser=OutputParser(p,config()['chunk_bytes'])
    primary=parser.render('primary',r,tmp_path/'run')
    views=parser.render('materialize',r,tmp_path/'direct')
    assert 'Ran 2 tests' in primary
    assert 'Ran 2 tests' in Path(views['selected']['path']).read_text()


def test_stream_contents_not_inherited_verdict(tmp_path):
    r=receipt(tmp_path/'run',err=b'FAILED (failures=1)\n');r['passed']=False;r['actual_exit_code']=1
    svc=ResultViews(config(),lambda *_:None);v=svc.capture(r,tmp_path/'run');svc.finish()
    assert r['passed'] is False and r['actual_exit_code']==1
    assert json.loads(Path(v['manifest']).read_text())['status']=='ready'


def test_raw_tamper_is_incident_not_false_command_failure(tmp_path):
    r=receipt(tmp_path/'run');Path(r['stdout']).write_text('modified')
    journal=[];s=ResultViews(config(),lambda n,d:journal.append((n,d)))
    v=s.capture(r,tmp_path/'run');errors=s.finish()
    assert v['status']=='error' and errors
    assert r['passed'] is True
    assert any(n=='incident.result_views' for n,d in journal)


def test_worker_failure_is_recorded_and_does_not_rerun_command(tmp_path,monkeypatch):
    import poise.infrastructure.result_views as module
    r=receipt(tmp_path/'run');s=ResultViews(config(),lambda *_:None)
    def fail(*a,**k):raise OSError('worker unavailable')
    monkeypatch.setattr(module.subprocess,'Popen',fail)
    view=s.capture(r,tmp_path/'run');inc=s.finish()
    assert inc and view['primary']
    assert json.loads(Path(view['manifest']).read_text())['status']=='error'
    assert r['passed'] is True


@pytest.mark.parametrize('key',['unknown','worker_timeout_seconds','chunk_bytes'])
def test_config_missing_values_not_defaulted(key):
    c=config();c.pop(key)
    with pytest.raises(PoiseError):OutputPolicy.parse(c)


def test_path_escape_and_duplicate_filenames_rejected():
    for bad in ('../full.txt','extended.txt'):
        c=config();c['unknown']['files']['full']=bad
        with pytest.raises(PoiseError):OutputPolicy.parse(c)


def test_ambiguous_known_match_rejected():
    c=config();c['known'].append(deepcopy(c['known'][0]));c['known'][1]['id']='other'
    p=OutputPolicy.parse(c)
    with pytest.raises(PoiseError):p.select(['python','-m','unittest'])


def test_bad_regex_rejected_before_command_execution():
    c=config();c['known'][0]['selection_pattern']='['
    with pytest.raises(PoiseError):OutputPolicy.parse(c)


def test_two_calls_do_not_share_results(tmp_path):
    s=ResultViews(config(),lambda *_:None)
    a=s.capture(receipt(tmp_path/'a',b'A'),tmp_path/'a')
    b=s.capture(receipt(tmp_path/'b',b'B'),tmp_path/'b');s.finish()
    assert a['manifest']!=b['manifest']
    ma=json.loads(Path(a['manifest']).read_text());mb=json.loads(Path(b['manifest']).read_text())
    assert Path(ma['representations']['full']['path']).read_bytes()!=Path(mb['representations']['full']['path']).read_bytes()


def test_known_lines_profile_does_not_fall_back_to_other_selection(tmp_path):
    from poise.infrastructure.result_views import OutputParser
    r=receipt(tmp_path/'run',out=b'unmatched output',err=b'other diagnostic')
    parser=OutputParser(OutputPolicy.parse(config()).select(r['argv']),config()['chunk_bytes'])
    assert parser.render('primary',r,tmp_path/'run')==''
    views=parser.render('materialize',r,tmp_path/'views')
    assert Path(views['full']['path']).read_bytes()==b'unmatched outputother diagnostic'
