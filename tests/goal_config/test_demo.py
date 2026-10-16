import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _demo_module(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / 'examples'))
    import demo
    return demo


def test_demo_fixture_declares_stage_authority_and_project_resources(tmp_path, monkeypatch):
    """Subject: real demo setup and native project registration, isolated data."""
    from poise.common import load_config
    from poise.composition import project_tools

    directory = tmp_path / 'fixture'
    home = _demo_module(monkeypatch).create(directory)
    root, cfg, workflows = load_config(home / 'project.json')
    stages = workflows['development']['stages']
    assert [(s['id'], s['purpose']) for s in stages] == [
        ('tests', 'execution'), ('test_review', 'observation'),
        ('implementation', 'execution'), ('code_review', 'observation'),
    ]
    assert all(s['criteria_authority'] == {'propose': [], 'decide': []} for s in stages)
    assert all(s['handler'] == 'produce' and s['role'] == 'executor' for s in stages)
    setup = Path(cfg['runtime_resources']['project_setup'])
    assert setup.is_absolute() and setup.is_relative_to(directory)
    registered = project_tools(setup).list()['projects']
    assert [(p['project'], p['config_path']) for p in registered] == [
        ('demo', str(home / 'project.json')),
    ]
    state = (root / cfg['paths']['state']).resolve()
    assert state.is_relative_to(directory)
    for option in ('database', 'lock', 'requirements_database', 'requirements_lock', 'worktrees'):
        assert (state / cfg['paths'][option]).resolve().is_relative_to(directory)
    assert (root / cfg['workflows']['development']).resolve().is_relative_to(directory)


def test_demo_refuses_existing_directory_without_overwrite(tmp_path, monkeypatch):
    directory = tmp_path / 'existing'
    directory.mkdir()
    sentinel = directory / 'operator.txt'
    sentinel.write_text('Existing operator data.\n')
    with pytest.raises(FileExistsError):
        _demo_module(monkeypatch).create(directory)
    assert sentinel.read_text() == 'Existing operator data.\n'
    assert sorted(p.name for p in directory.iterdir()) == ['operator.txt']


def test_generated_config_short_route_red_green_and_explicit_gate(tmp_path):
    root=Path(__file__).resolve().parents[2]
    unrelated = tmp_path / 'unrelated-cwd'
    unrelated.mkdir()
    r=subprocess.run([sys.executable,str(root/'examples/goal_config_demo.py'),'--directory',str(tmp_path/'demo')],
        cwd=unrelated, env={**os.environ,'PYTHONPATH':str(root/'src')},text=True,capture_output=True,timeout=40)
    assert r.returncode==0,r.stdout+r.stderr
    report=json.loads((tmp_path/'demo/goal-config-report.json').read_text())
    assert report['status']=='PASS' and report['new_exit_gate_blocks_incomplete_stage']
    assert report['replayed_without_duplicate'] and report['source_template_unchanged']
    assert report['created_revision'] != report['edited_revision']
    assert report['stages'] == ['tests', 'test_review', 'implementation', 'code_review']
    assert report['red_exit'] == 1 and report['green_exit'] == 0
    assert report['task_status'] == 'completed'
    assert report['base_branch_unchanged'] is True
    app = tmp_path / 'demo/application'
    branch = subprocess.check_output(['git', '-C', str(app), 'symbolic-ref', '--short', 'HEAD'], text=True)
    assert branch.strip() == 'main'
    original = subprocess.check_output(['git', '-C', str(app), 'show', 'main:src/double.py'], text=True)
    assert original == 'def double(n):\n    return n+1\n'
    assert list(unrelated.iterdir()) == []
