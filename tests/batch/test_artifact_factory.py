from copy import deepcopy
from pathlib import Path
import pytest
from harness.modules.artifact_factory.domain import ArtifactPlan
from harness.infrastructure.artifact_factory import FileArtifactFactory
from harness.common import HarnessError
from .helpers import batch_config,text_artifact


def factory(tmp_path):
    cfg=batch_config()
    roots={s:tmp_path/s for s in ('runtime','task','sprint')}
    for r in roots.values(): r.mkdir()
    return FileArtifactFactory(roots,cfg,tmp_path/'factory.lock',2.0,0.01),roots


def test_render_text_and_explicit_template_without_default():
    cfg=batch_config()
    specs=[text_artifact(),{'scope':'sprint','path':'brief.md','source':{'kind':'template','id':'note','version':'1','values':{'title':'План','body':'Текст'}}}]
    plan=ArtifactPlan.parse(specs,cfg)
    assert plan.items[0].content=='Результат'
    assert plan.items[1].content=='# План\n\nТекст\n'


@pytest.mark.parametrize('change',[
    lambda x:x['source'].update(id='missing'),
    lambda x:x['source'].update(version='2'),
    lambda x:x['source']['values'].pop('body'),
    lambda x:x['source']['values'].update(extra='ignored?'),
])
def test_template_invalid_before_files(change):
    spec={'scope':'task','path':'a.md','source':{'kind':'template','id':'note','version':'1','values':{'title':'A','body':'B'}}}
    change(spec)
    with pytest.raises(HarnessError): ArtifactPlan.parse([spec],batch_config())


@pytest.mark.parametrize('path',['../outside.md','/etc/fake','a/../../outside','.', 'a\\b'])
def test_factory_rejects_escape(tmp_path,path):
    f,roots=factory(tmp_path)
    with pytest.raises(HarnessError): f.prepare([text_artifact(path=path)])
    assert not list(roots['task'].rglob('*.md'))


def test_create_many_all_scopes_and_repeat(tmp_path):
    f,_=factory(tmp_path)
    items=[text_artifact(s,f'{i}.md',f'v{i}') for i,s in enumerate(('runtime','task','sprint'))]
    first=f.materialize(f.prepare(items));second=f.materialize(f.prepare(items))
    assert first==second and len(first)==3
    assert [Path(x).read_text() for x in first]==['v0','v1','v2']


def test_batch_preflight_rejects_last_conflict_without_first_write(tmp_path):
    f,roots=factory(tmp_path)
    existing=roots['task']/'artifacts/occupied.md';existing.parent.mkdir();existing.write_text('old')
    with pytest.raises(HarnessError):
        f.prepare([text_artifact(path='new.md'),text_artifact(path='occupied.md')])
    assert not (existing.parent/'new.md').exists() and existing.read_text()=='old'


def test_symlink_parent_cannot_redirect_creation(tmp_path):
    f,roots=factory(tmp_path)
    outside=tmp_path/'outside';outside.mkdir()
    (roots['task']/'artifacts').symlink_to(outside,target_is_directory=True)
    with pytest.raises(HarnessError): f.prepare([text_artifact()])
    assert list(outside.iterdir())==[]


def test_duplicate_same_destination_dedup_different_content_rejected(tmp_path):
    f,_=factory(tmp_path); x=text_artifact()
    assert len(f.materialize(f.prepare([x,deepcopy(x)])))==1
    with pytest.raises(HarnessError):f.prepare([x,text_artifact(text='Other')])


def test_missing_sprint_root_not_fallback(tmp_path):
    f,roots=factory(tmp_path); del f.roots['sprint']
    with pytest.raises(HarnessError):f.prepare([text_artifact('sprint')])


def test_oversized_artifact_rejected_not_truncated():
    cfg=batch_config();cfg['max_artifact_bytes']=2
    with pytest.raises(HarnessError):ArtifactPlan.parse([text_artifact(text='яа')],cfg)
