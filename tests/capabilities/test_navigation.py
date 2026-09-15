"""C026.2: exact inspected-copy identity and bounded, honest fallback."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from poise.common import PoiseError
from poise.application.navigation import CodeNavigation
from poise.infrastructure.capabilities import LocalProbeExecutor
from poise.infrastructure.navigation import NavigationFiles


def request(tmp_path):
    checkout = tmp_path / 'copy outside poise'
    checkout.mkdir()
    (checkout / 'pyproject.toml').write_text('[project]\nname="ai-poise"\n')
    (checkout / 'sample.py').write_text('def candidate(): return 7\n')
    return {'checkout': str(checkout), 'operation': 'symbol', 'files': ['sample.py'],
            'providers': [provider('ide'), provider('ast')]}


def provider(kind):
    return {'id': kind, 'kind': kind, 'operations': ['symbol', 'structural'],
            'identity_path': ['checkout'], 'digests_path': ['indexed_files'],
            'probe': {'id': kind, 'kind': 'command', 'required': True,
                      'argv': [sys.executable, '-c', 'print(7)'], 'cwd': str(Path.cwd()),
                      'environment': {}, 'timeout_seconds': 4, 'max_output_bytes': 4096,
                      'expected_exit_code': 0, 'stdout_contains': [], 'stderr_contains': [],
                      'json_assertions': [], 'mcp': None, 'tool_ref': kind,
                      'project_bound': False, 'remediation': 'Configure a read-only provider'}}


class Executor:
    def __init__(self, statuses=None, after=None):
        self.statuses = statuses or {}; self.calls = []; self.after = after

    def observe(self, spec, directory):
        self.calls.append((copy.deepcopy(spec.data), directory))
        if self.after: self.after()
        status = self.statuses.get(spec.data['id'], 'available')
        return {'status': status, 'receipt': 'local/' + spec.data['id'],
                'reason': 'peer did not establish binding or indexed contents'}


def test_ide_first_concrete_directory_and_content_assertion(tmp_path):
    req = request(tmp_path); peer = Executor()
    result = CodeNavigation(peer, NavigationFiles()).run(req)
    assert result['status'] == 'proved'
    assert result['provider'] == 'ide'
    assert [v[0]['id'] for v in peer.calls] == ['ide']
    assert peer.calls[0][1] == req['checkout']
    assert peer.calls[0][0]['cwd'] == str(Path.cwd())  # no harness cwd switching
    assert peer.calls[0][0]['json_assertions'] == [
        {'path': ['checkout'], 'equals': '${workspace}'},
        {'path': ['indexed_files'], 'equals': {'sample.py': hashlib.sha256(b'def candidate(): return 7\n').hexdigest()}}]
    assert result['scope'] == ['sample.py']


def test_ast_fallback_records_why_ide_did_not_prove_operation(tmp_path):
    peer = Executor({'ide': 'unavailable'})
    result = CodeNavigation(peer, NavigationFiles()).run(request(tmp_path))
    assert result['provider'] == 'ast'
    assert result['observations'][0]['status'] == 'unavailable'
    assert result['observations'][0]['reason']


def test_structural_uses_ast_directly(tmp_path):
    req = request(tmp_path); req['operation'] = 'structural'; peer = Executor()
    assert CodeNavigation(peer, NavigationFiles()).run(req)['provider'] == 'ast'
    assert [v[0]['id'] for v in peer.calls] == ['ast']


def test_ast_is_not_an_ide_inspection_substitute(tmp_path):
    req = request(tmp_path); req['operation'] = 'inspection'
    req['providers'][1]['operations'].append('inspection')
    peer = Executor()
    result = CodeNavigation(peer, NavigationFiles()).run(req)
    assert result['status'] == 'unavailable'
    assert peer.calls == []


def test_edit_during_observation_is_inconclusive_not_fallback(tmp_path):
    req = request(tmp_path)
    peer = Executor(after=lambda: (Path(req['checkout']) / 'sample.py').write_text('changed'))
    result = CodeNavigation(peer, NavigationFiles()).run(req)
    assert result['status'] == 'inconclusive'
    assert result['reason'] == 'inspected files changed during observation'
    assert len(peer.calls) == 1


@pytest.mark.parametrize('bad', ['../elsewhere.py', '/etc/passwd', 'missing.py'])
def test_invalid_subject_paths_do_not_execute(tmp_path, bad):
    req = request(tmp_path); req['files'] = [bad]; peer = Executor()
    with pytest.raises(PoiseError): CodeNavigation(peer, NavigationFiles()).run(req)
    assert peer.calls == []


def test_non_ai_poise_copy_rejected(tmp_path):
    req = request(tmp_path)
    (Path(req['checkout']) / 'pyproject.toml').write_text('[project]\nname="erp"\n')
    with pytest.raises(PoiseError): CodeNavigation(Executor(), NavigationFiles()).run(req)


def test_entire_batch_validated_before_first_probe(tmp_path):
    req = request(tmp_path); req['providers'][1]['probe']['timeout_seconds'] = -1
    peer = Executor()
    with pytest.raises(PoiseError): CodeNavigation(peer, NavigationFiles()).run(req)
    assert peer.calls == []


@pytest.mark.parametrize('wrong', ['identity', 'digest', 'none'])
def test_real_readonly_peer_checks_identity_and_indexed_bytes(tmp_path, wrong):
    req = request(tmp_path); req['providers'] = [provider('ast')]
    supplied = {'checkout': req['checkout'], 'indexed_files': {
        'sample.py': hashlib.sha256(b'def candidate(): return 7\n').hexdigest()}, 'symbols': ['candidate']}
    if wrong == 'identity': supplied['checkout'] = str(tmp_path / 'wrong')
    if wrong == 'digest': supplied['indexed_files']['sample.py'] = '0' * 64
    req['providers'][0]['probe']['argv'] = [sys.executable, '-c', 'print(' + repr(json.dumps(supplied)) + ')']
    executor = LocalProbeExecutor(tmp_path / 'receipts', 0o600,
        {'stdout': 'stdout', 'stderr': 'stderr', 'receipt': 'receipt.json'})
    result = CodeNavigation(executor, NavigationFiles()).run(req)
    assert result['status'] == ('proved' if wrong == 'none' else 'unavailable')
    assert sorted(p.name for p in Path(req['checkout']).iterdir()) == ['pyproject.toml', 'sample.py']


def test_symlink_escape_rejected_before_any_call(tmp_path):
    req = request(tmp_path); outside = tmp_path / 'foreign.py'; outside.write_text('secret')
    (Path(req['checkout']) / 'link.py').symlink_to(outside)
    req['files'] = ['link.py']; peer = Executor()
    with pytest.raises(PoiseError): CodeNavigation(peer, NavigationFiles()).run(req)
    assert peer.calls == []


def test_all_unavailable_retains_both_receipts(tmp_path):
    result = CodeNavigation(Executor({'ide': 'unavailable', 'ast': 'unavailable'}), NavigationFiles()).run(request(tmp_path))
    assert result['status'] == 'unavailable'
    assert [o['receipt'] for o in result['observations']] == ['local/ide', 'local/ast']
