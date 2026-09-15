"""C025: retain native runner ownership; add only an unsupported-host guard."""
import json
import os
from pathlib import Path
import sys

import pytest
from poise.common import PoiseError
from poise.execution import RegisteredCheckRunner


def test_exact_environment_argv_and_relative_cwd(tmp_path, monkeypatch):
    (tmp_path / 'copy with spaces').mkdir()
    monkeypatch.chdir(tmp_path)
    runner = RegisteredCheckRunner()
    result = runner.run('exact', [sys.executable, '-c',
        'import os,sys,json; print(json.dumps([os.getcwd(),os.getenv("ONLY"),sys.argv[1:]]))',
        'a; echo injected', 'x y'], Path('copy with spaces'), {'ONLY': 'literal'}, 5,
        tmp_path / 'observations/out', tmp_path / 'observations/err')
    assert result['actual_exit_code'] == 0
    assert json.loads(Path(result['stdout']).read_text()) == [
        str(tmp_path / 'copy with spaces'), 'literal', ['a; echo injected', 'x y']]
    assert list((tmp_path / 'copy with spaces').iterdir()) == []
    assert Path.cwd() == tmp_path
    assert runner.active_ids() == ()


def test_missing_executable_is_a_clear_error_and_releases_ownership(tmp_path):
    runner = RegisteredCheckRunner()
    with pytest.raises(PoiseError, match='no-such-ai-poise-executable'):
        runner.run('missing', ['/no-such-ai-poise-executable'], tmp_path, {}, 5,
                   tmp_path/'out', tmp_path/'err')
    assert runner.active_ids() == ()


def test_unsupported_host_rejected_before_spawn_and_log_creation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.delattr(os, 'killpg')
    import poise.execution as execution
    monkeypatch.setattr(execution.subprocess, 'Popen', lambda *a, **kw: calls.append(kw))
    with pytest.raises(PoiseError, match='POSIX process groups'):
        RegisteredCheckRunner().run('unsupported', [sys.executable, '-c', 'pass'], tmp_path,
                                     {}, 5, tmp_path/'out', tmp_path/'err')
    assert calls == []
    assert list(tmp_path.iterdir()) == []
