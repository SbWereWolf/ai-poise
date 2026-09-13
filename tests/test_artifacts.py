from pathlib import Path
import pytest
from conftest import fill, add_test, write_json
from poise.runtime import PoiseError
from conftest import Poise


def begin(project):
    h = Poise(project['config_path'], 'A'); b = h.bootstrap(task_file=project['task_path'])
    add_test(b['worktree'])
    return h, b


def test_only_path_is_needed_and_id_is_stable(project):
    h, b = begin(project)
    f = Path(b['task_root']) / 'report.md'; f.write_text('Результат')
    fill(b, artifacts=[str(f), str(f)])
    first = h.verify(); assert len(first['artifacts']) == 1
    assert set(first['artifacts'][0]) == {'id', 'path'}
    assert Path(first['artifacts'][0]['path']).is_file()
    assert h.verify()['artifacts'] == first['artifacts']


def test_sprint_artifact_in_sprint_root(project):
    from sprints.helpers import publish_existing_contract
    h=Poise(project['config_path'],'A')
    contract=publish_existing_contract(h,project['task'],'S1')
    write_json(project['task_path'],contract)
    b=h.bootstrap(task_file=project['task_path']);add_test(b['worktree'])
    f = Path(b['sprint_root']) / 'shared.json'; f.write_text('{}')
    fill(b, artifacts=[str(f)])
    assert h.verify()['artifacts'][0]['path'] == str(f.resolve())


def test_external_path_rejected(project, tmp_path):
    h, b = begin(project)
    f = tmp_path / 'outside.md'; f.write_text('outside')
    fill(b, artifacts=[str(f)])
    with pytest.raises(PoiseError, match='root|root|област'):
        h.verify()


def test_symlink_escape_rejected(project, tmp_path):
    h, b = begin(project)
    outside = tmp_path / 'secret'; outside.write_text('outside')
    link = Path(b['task_root']) / 'bad'; link.symlink_to(outside)
    fill(b, artifacts=[str(link)])
    with pytest.raises(PoiseError): h.verify()


def test_foreign_task_path_rejected(project):
    h, b = begin(project)
    f = Path(b['task_root']).parent / 'T2' / 'file'; f.parent.mkdir(); f.write_text('foreign')
    fill(b, artifacts=[str(f)])
    with pytest.raises(PoiseError): h.verify()


def test_configured_file_count_is_checked(project):
    project['process']['stages'][0]['artifact_requirements'] = [
        {'scope': 'task', 'pattern': '*.json', 'minimum': 2, 'maximum': 2}]
    write_json(project['root'] / 'config/processes/development.json', project['process'])
    h, b = begin(project)
    a = Path(b['task_root']) / 'a.json'; a.write_text('{}')
    fill(b, artifacts=[str(a), str(a)])
    with pytest.raises(PoiseError, match='количество'): h.verify()
    c = Path(b['task_root']) / 'b.json'; c.write_text('{}')
    fill(b, artifacts=[str(a), str(c)])
    assert len(h.verify()['artifacts']) == 2


def test_runtime_artifact_is_not_a_durable_task_artifact(project):
    h, b = begin(project)
    f = Path(b['runtime_root']) / 'scratch.txt'; f.write_text('tmp')
    fill(b, artifacts=[str(f)])
    r = h.verify()
    assert r['status'] == 'verified'
    assert r['artifacts'] == []  # final links never point to removed runtime
    assert not f.exists()


def test_missing_artifact_rejected_before_tests(project):
    h, b = begin(project)
    fill(b, artifacts=[str(Path(b['task_root']) / 'missing')])
    with pytest.raises(PoiseError): h.verify()
    assert h.show()['attempts'] == 0


def test_task_owner_root_cannot_redirect_to_external_directory(project,tmp_path):
    outside=tmp_path/'other-owner';outside.mkdir()
    parent=project['root']/'state/standalone';parent.mkdir(parents=True)
    (parent/'T1').symlink_to(outside,target_is_directory=True)
    h=Poise(project['config_path'],'A')
    with pytest.raises(PoiseError,match='symlink'):
        h.bootstrap(task_file=project['task_path'])
