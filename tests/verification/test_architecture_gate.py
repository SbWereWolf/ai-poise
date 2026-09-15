"""AI-poise-only static boundary checks with actionable and source-bound evidence."""
import json
from pathlib import Path
import subprocess
import sys
import os

import pytest
from poise.common import PoiseError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checker(tmp_path):
    from poise.infrastructure.architecture_checks import AiPoiseArchitectureChecks
    checkout = tmp_path / 'external folder'; checkout.mkdir()
    (checkout / 'pyproject.toml').write_text('[project]\nname="ai-poise"\nversion="1"\n')
    rule = {'id': 'domain-rule', 'paths': ['src/poise/modules/*/domain.py'],
        'forbidden_imports': ['os', 'subprocess', 'poise.application', 'poise.infrastructure'],
        'forbidden_calls': ['open', 'builtins.open', 'eval', 'exec', '__import__', 'importlib.import_module'],
        'protected_subscripts': {'variables': [], 'fields': []},
        'protected_attributes': [], 'forbidden_sql_tables': []}
    runtime_rule = {'id': 'task-owner', 'paths': ['src/poise/runtime.py'],
        'forbidden_imports': [], 'forbidden_calls': [],
        'protected_subscripts': {'variables': ['task', 'data'], 'fields': ['status', 'claimed_by']},
        'protected_attributes': ['status'], 'forbidden_sql_tables': ['tasks', 'submissions']}
    path = tmp_path / 'running-policy.json'
    path.write_text(json.dumps({'schema': 'ai-poise-architecture-boundaries-1', 'rules': [rule, runtime_rule]}))
    return AiPoiseArchitectureChecks.load(path), checkout, path


def put(checkout, name, text):
    p = checkout / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    return p


@pytest.mark.parametrize('text,symbol', [
    ('import os as operating\n', 'os'),
    ('from subprocess import run as invoke\n', 'subprocess.run'),
    ('from ...infrastructure.work import Work\n', 'poise.infrastructure.work.Work'),
    ('from ... import application\n', 'poise.application'),
    ('import builtins as b\nb.open("a")\n', 'builtins.open'),
    ('from builtins import open as read\nread("a")\n', 'builtins.open'),
    ('import importlib as loader\nloader.import_module("poise.infrastructure.work")\n', 'importlib.import_module'),
])
def test_domain_rejects_known_io_and_layer_access(checker, text, symbol):
    checks, copy, _ = checker
    name = 'src/poise/modules/subject/domain.py'; put(copy, name, text)
    result = checks.check(copy, [name])
    assert result['status'] == 'failed' and result['passed'] is False
    assert any(d['symbol'] == symbol and d['rule'] == 'domain-rule' and d['file'] == name
               and d['line'] in (1, 2) for d in result['diagnostics'])
    assert result['checked_files'][0]['path'] == name
    assert len(result['checked_files'][0]['sha256']) == 64


def test_valid_pure_source_passes_without_executing_it(checker):
    checks, copy, _ = checker
    name = 'src/poise/modules/subject/domain.py'
    put(copy, name, 'from dataclasses import dataclass\nraise RuntimeError("must not execute")\n')
    result = checks.check(copy, [name])
    assert result['status'] == 'passed' and result['diagnostics'] == []


@pytest.mark.parametrize('source,symbol', [
    ('data["status"] = "verified"\n', 'data.status'),
    ('del task["claimed_by"]\n', 'task.claimed_by'),
    ('task["status"]: str = "active"\n', 'task.status'),
    ('task.update(status="done")\n', 'task.status'),
    ('task.update({"status": "done"})\n', 'task.status'),
    ('self.status = "done"\n', 'status'),
    ('query = "UPDATE tasks SET status=1"\n', 'tasks'),
    ('query = "insert into submissions values (1)"\n', 'submissions'),
])
def test_runtime_does_not_take_task_lifecycle_or_sql_ownership(checker, source, symbol):
    checks, copy, _ = checker; name = 'src/poise/runtime.py'; put(copy, name, source)
    result = checks.check(copy, [name])
    assert result['status'] == 'failed'
    assert any(d['symbol'] == symbol and d['rule'] == 'task-owner' for d in result['diagnostics'])


def test_syntax_missing_and_explicit_deletion_have_distinct_outcomes(checker):
    checks, copy, _ = checker; name = 'src/poise/modules/subject/domain.py'
    put(copy, name, 'def broken(:\n')
    assert checks.check(copy, [name])['status'] == 'inconclusive'
    (copy / name).unlink()
    assert checks.check(copy, [name])['status'] == 'inconclusive'
    result = checks.check(copy, [name], deleted_paths=[name])
    assert result['status'] == 'passed' and result['deleted_paths'] == [name]
    put(copy, name, 'VALUE=1\n')
    with pytest.raises(PoiseError): checks.check(copy, [name], deleted_paths=[name])


@pytest.mark.parametrize('path', ['../file.py', '/tmp/file.py', 'src/**', './src/poise/runtime.py'])
def test_arbitrary_subject_paths_cannot_escape_or_expand(checker, path):
    checks, copy, _ = checker
    with pytest.raises(PoiseError): checks.check(copy, [path])


def test_symlink_escape_and_other_application_rejected(checker, tmp_path):
    checks, copy, _ = checker
    (copy / 'src').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PoiseError): checks.check(copy, ['src/poise/runtime.py'])
    (copy / 'pyproject.toml').write_text('[project]\nname="erp"\n')
    with pytest.raises(PoiseError, match='ai-poise'): checks.check(copy, [])


def test_non_scope_source_is_explicitly_unchecked_not_multilanguage(checker):
    checks, copy, _ = checker
    result = checks.check(copy, ['web/component.js'])
    assert result['unchecked_paths'] == ['web/component.js']
    assert result['checked_files'] == []


def test_policy_errors_do_not_become_a_silent_pass(checker):
    from poise.infrastructure.architecture_checks import AiPoiseArchitectureChecks
    _, _, path = checker
    raw = json.loads(path.read_text()); raw['rules'][0]['paths'] = ['../erp/**']; path.write_text(json.dumps(raw))
    with pytest.raises(PoiseError): AiPoiseArchitectureChecks.load(path)


def test_cli_uses_same_rule_diagnostics_from_an_arbitrary_cwd(checker, tmp_path):
    checks, copy, policy = checker
    name = 'src/poise/modules/subject/domain.py'; put(copy, name, 'import os\n')
    proc = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/check_architecture.py'),
        '--checkout', str(copy), '--policy', str(policy), '--path', name],
        cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')}, text=True, capture_output=True)
    assert proc.returncode == 1, proc.stderr
    assert json.loads(proc.stdout) == checks.check(copy, [name])


def test_shipped_boundary_policy_passes_current_covered_sources():
    from poise.infrastructure.architecture_checks import AiPoiseArchitectureChecks
    checks = AiPoiseArchitectureChecks.load(ROOT / 'config/testing/architecture-boundaries.json')
    result = checks.check(ROOT, checks.covered_paths(ROOT))
    assert result['status'] == 'passed', result['diagnostics']
    assert result['checked_files']


def test_observed_source_change_is_inconclusive(checker, monkeypatch):
    from poise.infrastructure import architecture_checks
    checks, copy, _ = checker
    name = 'src/poise/modules/subject/domain.py'; target = put(copy, name, 'VALUE=1\n')
    original = architecture_checks._bytes
    def read_and_edit(path):
        data = original(path)
        target.write_text('VALUE=2\n')
        return data
    monkeypatch.setattr(architecture_checks, '_bytes', read_and_edit)
    result = checks.check(copy, [name])
    assert result['status'] == 'inconclusive' and result['passed'] is False
    assert any(d['kind'] == 'source_changed' for d in result['diagnostics'])


def test_shipped_routing_registers_the_same_explicit_boundary_policy():
    from poise.infrastructure.development_routing import load_development_routing
    routing = load_development_routing(catalog=ROOT / '.agents/skill-catalog.json',
        selection=ROOT / 'config/development/skill-selection.json',
        policy=ROOT / 'config/development/routing.json', packages=ROOT / 'config/testing/test-packages.json')
    result = routing.check_authoring_boundaries(str(ROOT), 'produce', ['src/poise/runtime.py'], [])
    assert result['status'] == 'passed'
    assert result['policy_sha256'] == routing.files.configuration_digests['architecture']
    assert 'architecture-boundaries.md#проверка-архитектуры-перед-авторской-сдачей' in (ROOT / 'src/AGENTS.md').read_text()


def test_all_enumeration_agrees_with_existing_boundary_path_matching(checker):
    checks, copy, _ = checker
    name = 'src/poise/modules/subject/nested/domain.py'
    put(copy, name, 'import os\n')
    assert checks.policy.applicable(name)
    assert name in checks.covered_paths(copy)
    assert checks.check(copy, checks.covered_paths(copy))['status'] == 'failed'
