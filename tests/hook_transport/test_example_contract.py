"""Runnable examples must use the installed public contract, not compatibility aliases."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from poise.common import load_config


def demo_module(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / 'examples'))
    spec = importlib.util.spec_from_file_location('example_contract_demo', root / 'examples/demo.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_demo_configuration_is_current_and_does_not_create_a_remote(tmp_path, monkeypatch):
    module = demo_module(monkeypatch)
    home = module.create(tmp_path / 'example')
    _, config, _ = load_config(home / 'project.json')
    assert config['git']['push_required'] is False
    assert 'standalone_tasks' in config['paths'] and 'tasks' not in config['paths']
    result = subprocess.run(['git', '-C', config['git']['repository'], 'remote'],
                            capture_output=True, text=True, check=True, timeout=5)
    assert result.stdout == ''
    assert not (tmp_path / 'example/remote.git').exists()


def test_standalone_demo_reaches_real_red_and_green_with_local_caller(tmp_path):
    root = Path(__file__).resolve().parents[2]
    target = tmp_path / 'standalone'
    # Deliberately contaminated parent values must not bind the real user's session.
    env = {**os.environ, 'PYTHONPATH': str(root/'src'),
           'CODEX_SESSION_ID': 'user-session', 'CODEX_THREAD_ID': 'other-user-thread',
           'POISE_SESSION': 'not-a-supported-caller'}
    result = subprocess.run([sys.executable, '-B', str(root/'examples/demo.py'),
                             '--directory', str(target)], capture_output=True, text=True,
                            env=env, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((target/'demo-report.json').read_text())
    assert report['status'] == 'PASS' and report['task_status'] == 'completed'
    assert (report['red_exit'], report['green_exit']) == (1, 0)
    assert report['base_branch_unchanged'] is True
    assert (target/'poise/demo-caller.json').is_file()


def test_demo_never_overwrites_existing_directory(tmp_path, monkeypatch):
    import pytest
    module = demo_module(monkeypatch)
    target = tmp_path / 'existing'
    target.mkdir()
    marker = target / 'user.txt'
    marker.write_text('keep user data')
    with pytest.raises(FileExistsError):
        module.create(target)
    assert marker.read_text() == 'keep user data'
    assert sorted(p.name for p in target.iterdir()) == ['user.txt']
