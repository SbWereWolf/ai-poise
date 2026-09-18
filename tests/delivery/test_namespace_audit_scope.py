"""Rename auditing covers executable namespaces, not unrelated historical data."""
from pathlib import Path
import pytest
from tests.delivery import test_poise_namespace as audit

OLD = 'har' + 'ness'

@pytest.mark.parametrize('text', [
    f'import {OLD}\n', f'from {OLD}.runtime import Work\n',
    f'class {OLD.title()}: pass\n', f'error = {OLD.title()}Error()\n',
    f'import os\nos.getenv("{OLD.upper()}_CONFIG")\n',
    f'import importlib\nimportlib.import_module("{OLD}.runtime")\n',
    f'import subprocess\nsubprocess.run(["python", "-m", "{OLD}"])\n',
    f'import subprocess\nCOMMAND="python -m {OLD}"\nsubprocess.run(COMMAND, shell=True)\n',
])
def test_live_python_namespace_is_rejected(text):
    assert audit._live_namespace_hits('src/poise/live.py', text)

@pytest.mark.parametrize(('path','text'), [
    ('README.md', f'python -m {OLD} --help'),
    ('tools/launch.sh', f'{OLD} --help\n'),
    ('pyproject.toml', f'{OLD} = "{OLD}:main"\n'),
    ('config/hooks.json', '{"command":"' + OLD + '"}'),
])
def test_live_entry_points_are_rejected(path, text):
    assert audit._live_namespace_hits(path, text)

@pytest.mark.parametrize(('path','text'), [
    ('AGENTS.md', 'Repair the task ' + OLD + ' only when authorized.'),
    ('src/poise/migration.py', 'TASK_ID = "ERP-' + OLD.upper() + '-COMPARISON-PLAN"\n'),
    ('tests/migration_fixture.py', 'replacement = command.replace("-m poise", "-m ' + OLD + '")\n'),
    ('docs/runtime-hooks.md', '`' + OLD + '-main` is an immutable previous installation id.'),
])
def test_ordinary_noun_historical_identity_and_negative_fixture_are_not_imports(path,text):
    assert audit._live_namespace_hits(path, text) == []


def test_operational_history_is_excluded_but_live_untracked_source_is_checked(tmp_path):
    content = 'import ' + OLD + '\n'
    for relative in ('src/poise/new.py', 'tests/test_live.py', 'projects/state/artifacts/old.py',
                     'RECOVERY-MANIFEST.json', 'WORKLOG.md', 'HANDOFF.md', '.git/old.py'):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    observed = {relative for _, relative in audit._files(tmp_path)}
    assert observed == {'src/poise/new.py', 'tests/test_live.py'}

@pytest.mark.parametrize('text', [
    OLD + ' или подделывать его статусы нельзя.',
    'Состояние кода отдельно от lifecycle ' + OLD + '.',
])
def test_plain_prose_at_line_start_and_sentence_end_is_not_a_command(text):
    assert audit._live_namespace_hits('docs/current.md', text) == []

@pytest.mark.parametrize('text', [
    f'import subprocess as sp\nsp.run(["python", "-m", "{OLD}"])\n',
    f'from subprocess import run as execute\nexecute(args=["{OLD}", "--help"])\n',
    f'from importlib import import_module as load\nload("{OLD}.runtime")\n',
])
def test_imported_execution_aliases_are_not_an_escape(text):
    assert audit._live_namespace_hits('src/poise/live.py', text)
