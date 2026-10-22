"""Physical protected Stage commits through the production public use case."""
import json
import pytest

from git_diff_checks import (admission, arrange, consumer, fixture, git, git_wrapper,
                             ref, saved, tree, tree_files, working, attempts)
from process_checks import write_json
from store_checks import snapshot
from apps.execution.shell import ExecutionError


@pytest.mark.parametrize('name', ['modify','delete','rename','untracked','index-working'])
def test_commit_consumer_creates_exact_real_commit_for_allowed_cases(tmp_path, monkeypatch, name):
    a = arrange(tmp_path, monkeypatch, name)
    method, args = consumer(a)
    db = snapshot(a['database'])
    before_work = working(a['root'])
    before = saved(a['root'])
    result = method(**args)
    assert set(result) == {'status','created','commit','tree','admission'}
    assert result['status'] == 'committed' and result['created'] is True
    assert result['commit'] == ref(a['root']) != a['head']
    assert result['tree'] == a['candidate'] == ref(a['root'],'HEAD^{tree}')
    admission(a, result['admission'], before=before)
    assert result['admission']['head_commit'] == a['head']
    assert tree_files(a['root'],'HEAD') == a['case']['expected']['candidate']
    assert ref(a['root'],'HEAD^') == a['head']
    assert git(a['root'],'show','-s','--format=%an%n%ae%n%cn%n%ce%n%B','HEAD').decode() == fixture()['commit_metadata']
    assert working(a['root']) == before_work and snapshot(a['database']) == db


@pytest.mark.parametrize('name', ['rename-extra','preentry-extra','stage-cancellation','readonly'])
def test_commit_consumer_denies_each_negative_union_without_effect(tmp_path, monkeypatch, name):
    a = arrange(tmp_path, monkeypatch, name)
    method, args = consumer(a)
    before, db = saved(a['root']), snapshot(a['database'])
    result = method(**args)
    assert set(result) == {'status','created','commit','tree','admission'}
    assert result['status'] == 'commit_denied' and result['created'] is False
    assert result['commit'] == a['head'] and result['tree'] == ref(a['root'],'HEAD^{tree}')
    admission(a,result['admission'], before=before)
    assert saved(a['root']) == before and snapshot(a['database']) == db


def test_commit_consumer_main_uses_selected_stage_contract(tmp_path, monkeypatch):
    a = arrange(tmp_path, monkeypatch, 'main-stage')
    method, args = consumer(a)
    before = saved(a['root'])
    denied = method(**args)
    assert denied['status'] == 'commit_denied' and denied['created'] is False
    admission(a,denied['admission'], before=before)
    assert saved(a['root']) == before
    accepted_before = saved(a['root'])
    accepted = method(**dict(args,stage_id='integration'))
    assert accepted['status'] == 'committed' and accepted['created'] is True
    admission(a,accepted['admission'],a['case']['expected']['results'][1], before=accepted_before)
    assert accepted['commit'] == ref(a['root']) != a['head']
    assert tree_files(a['root'],'HEAD') == a['case']['expected']['candidate']


def test_commit_consumer_empty_delta_preserves_separate_index(tmp_path, monkeypatch):
    a = arrange(tmp_path, monkeypatch, 'index-only')
    method, args = consumer(a)
    before, db = saved(a['root']), snapshot(a['database'])
    result = method(**args)
    assert result['status'] == 'committed' and result['created'] is False
    assert result['commit'] == a['head'] and result['tree'] == a['candidate']
    admission(a,result['admission'], before=before)
    assert saved(a['root']) == before and snapshot(a['database']) == db


def test_fixed_candidate_repeat_creates_no_duplicate_commit(tmp_path, monkeypatch):
    a = arrange(tmp_path, monkeypatch)
    method, args = consumer(a)
    first_before = saved(a['root'])
    first = method(**args)
    admission(a,first['admission'],before=first_before)
    assert first['status'] == 'committed' and first['created'] is True
    before = saved(a['root'])
    count = git(a['root'],'rev-list','--count','HEAD')
    second = method(**args)
    assert second['status'] == 'committed' and second['created'] is False
    assert second['commit'] == first['commit'] and second['tree'] == first['tree']
    assert second['admission']['commit_paths'] == []
    assert second['admission']['stage_paths'] == ['src/result.py']
    assert second['admission']['head_commit'] == before['head']
    assert second['admission']['head_tree'] == before['head_tree']
    assert git(a['root'],'rev-list','--count','HEAD') == count
    assert saved(a['root']) == before


@pytest.mark.parametrize('change', ['working','head'])
def test_commit_consumer_rechecks_candidate_and_head_before_staging(tmp_path, monkeypatch, change):
    a = arrange(tmp_path, monkeypatch)
    method, args = consumer(a)
    if change == 'working':
        (a['root']/'src/result.py').write_text('C\n')
    else:
        commit = git(a['root'],'commit-tree',ref(a['root'],'HEAD^{tree}'),
                     '-p',a['head'],'-m','Concurrent owner change').decode().strip()
        git(a['root'],'update-ref','HEAD',commit)
    before, db = saved(a['root']), snapshot(a['database'])
    with pytest.raises(ExecutionError) as failure:
        method(**args)
    assert str(failure.value)
    assert saved(a['root']) == before and snapshot(a['database']) == db


@pytest.mark.parametrize('field,value', [('generation',2),('context_id','missing'),
    ('stage_id','missing'),('root',{'kind':'code','position':9}),('author_name',''),
    ('author_email',''),('message',''),('commit_pattern','['),('commit_pattern','stage')])
def test_commit_consumer_preflight_uses_saved_scope_and_explicit_actor_message(tmp_path, monkeypatch, field, value):
    a = arrange(tmp_path, monkeypatch)
    method, args = consumer(a, **{field:value})
    before, db = saved(a['root']), snapshot(a['database'])
    try:
        method(**args)
    except ExecutionError as failure:
        assert str(failure)
    else:
        pytest.fail('Invalid declared precondition was accepted')
    assert saved(a['root']) == before and snapshot(a['database']) == db


@pytest.mark.parametrize('mode,effect', [('add','none'),('commit','none'),('commit-then-fail','head_changed')])
def test_commit_failure_reports_real_partial_effect(tmp_path, monkeypatch, mode, effect):
    a = arrange(tmp_path, monkeypatch, 'index-working')
    wrapper = git_wrapper(a,monkeypatch,mode)
    method, args = consumer(a)
    before_work = working(a['root'])
    db = snapshot(a['database'])
    try:
        method(**args)
    except ExecutionError as failure:
        assert failure.__class__.__name__ == 'StageCommitError'
        assert failure.phase == ('add' if mode == 'add' else 'commit')
        assert failure.effect == effect and failure.before_head == a['head']
        assert failure.after_head == ref(a['root'])
        assert failure.candidate_tree == a['candidate']
        assert 'controlled' in failure.detail and failure.detail in str(failure)
    else:
        pytest.fail('Real controlled Git failure was reported as success')
    assert attempts(wrapper,'commit') == (0 if mode == 'add' else 1)
    assert attempts(wrapper,'add',real_index=True) == 1
    if mode == 'add':
        assert tree_files(a['root'],git(a['root'],'write-tree').decode().strip()) == a['case']['given']['index']
    else:
        assert tree_files(a['root'],git(a['root'],'write-tree').decode().strip()) == a['case']['expected']['candidate']
    if effect == 'head_changed':
        assert ref(a['root']) != a['head']
        assert tree_files(a['root'],'HEAD') == a['case']['expected']['candidate']
    else:
        assert ref(a['root']) == a['head']
    assert working(a['root']) == before_work and snapshot(a['database']) == db


def test_commit_timeout_reports_unknown_without_retry(tmp_path, monkeypatch):
    a = arrange(tmp_path,monkeypatch)
    data = json.loads(a['config'].read_text())
    data['git']['seconds'] = 0.2
    write_json(a['config'],data)
    wrapper = git_wrapper(a,monkeypatch,'timeout')
    method,args = consumer(a)
    before_work = working(a['root'])
    try:
        method(**args)
    except ExecutionError as failure:
        assert failure.__class__.__name__ == 'StageCommitError'
        assert failure.phase == 'commit' and failure.effect == 'unknown'
        assert failure.before_head == a['head']
        assert failure.candidate_tree == a['candidate'] and failure.detail
    else:
        pytest.fail('Timed out Git commit was reported as success')
    assert attempts(wrapper,'commit') == 1
    assert ref(a['root']) == a['head']
    assert working(a['root']) == before_work
    assert tree_files(a['root'],git(a['root'],'write-tree').decode().strip()) == a['case']['expected']['candidate']


@pytest.mark.parametrize('mode,phase,effect', [
    ('index-mismatch','index','none'),('postcondition','postcondition','head_changed')])
def test_commit_rejects_wrong_index_and_successful_wrong_result(tmp_path, monkeypatch, mode, phase, effect):
    a = arrange(tmp_path,monkeypatch)
    unexpected = tree(a['root'],fixture()['unexpected_candidate'])
    monkeypatch.setenv('T052_UNEXPECTED_TREE',unexpected)
    wrapper = git_wrapper(a,monkeypatch,mode)
    method,args = consumer(a)
    before = saved(a['root'])
    db = snapshot(a['database'])
    try:
        method(**args)
    except ExecutionError as failure:
        assert failure.__class__.__name__ == 'StageCommitError'
        assert failure.phase == phase and failure.effect == effect
        assert failure.before_head == before['head']
        assert failure.after_head == ref(a['root'])
        assert failure.candidate_tree == a['candidate'] and failure.detail
    else:
        pytest.fail('Wrong indexed/result tree was accepted as intended commit')
    assert attempts(wrapper,'commit') == (0 if mode == 'index-mismatch' else 1)
    if mode == 'index-mismatch':
        assert ref(a['root']) == before['head']
        assert tree_files(a['root'],git(a['root'],'write-tree').decode().strip()) == a['case']['given']['head']
    else:
        assert ref(a['root']) != before['head']
        assert tree_files(a['root'],'HEAD') == fixture()['unexpected_candidate']
        assert tree_files(a['root'],git(a['root'],'write-tree').decode().strip()) == a['case']['expected']['candidate']
    assert working(a['root']) == before['working'] and snapshot(a['database']) == db


@pytest.mark.parametrize('name',['modify','rename-extra'])
def test_linked_worktree_protected_commit_preserves_neighbor_workspaces(tmp_path,monkeypatch,name):
    a = arrange(tmp_path,monkeypatch,name,linked=True)
    assert (a['root']/'.git').is_file()
    neighbors = [saved(root) for root in a['neighbors']]
    before = saved(a['root'])
    db = snapshot(a['database'])
    method,args = consumer(a)
    result = method(**args)
    allowed = a['case']['expected']['results'][0]['allowed']
    assert result['status'] == ('committed' if allowed else 'commit_denied')
    assert result['created'] is allowed
    admission(a,result['admission'],before=before)
    if allowed:
        assert result['commit'] == ref(a['root']) != before['head']
        assert result['tree'] == a['candidate']
        assert tree_files(a['root'],'HEAD') == a['case']['expected']['candidate']
    else:
        assert saved(a['root']) == before
    assert [saved(root) for root in a['neighbors']] == neighbors
    assert snapshot(a['database']) == db
