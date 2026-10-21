"""R3 integration regressions: current facts require specific, addressed actions."""
from copy import deepcopy
import json
import re
import subprocess

from tests.context_resume.helpers import Recovery, fixture


def git(r, *arguments):
    return subprocess.run(['git', '-C', str(r.s.checkout), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


def observe(r, source='compact'):
    value = r.restore(source)
    print('ACTUAL_CONTINUATION_CARD', json.dumps(value, ensure_ascii=False))
    assert value['status'] == 'ready'
    assert value['checkpoint']['state'] == 'restored'
    assert len(r.outputs[-1]['hookSpecificOutput']['additionalContext'].encode('utf-8')) <= 4096
    assert fixture()['body_marker'] not in json.dumps(value)
    return value


def action(value, owner):
    selected = [item for item in value['actions'] if item.startswith('restore:' + owner + ';')]
    assert selected, 'R3 requires a specific restore action for ' + owner
    return '\n'.join(selected)


def test_changed_readable_external_instruction_has_current_address(project, tmp_path, monkeypatch):
    r = Recovery(project, tmp_path, monkeypatch).enable()
    root = tmp_path / 'external-instructions'
    path = '.agents/skills/core/SKILL.md'
    for name in ('core', 'domain'):
        relative = '.agents/skills/' + name + '/SKILL.md'
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((r.s.checkout / relative).read_bytes())
    options = deepcopy(r.options)
    options['instructions']['root'] = str(root)
    r.configure(options)
    before = r.save()
    assert all(m['available'] for m in before['captured']['instructions']['materials'])
    unchanged_checkout = (r.s.checkout / path).read_bytes()
    (root / path).write_bytes(b'# Changed external required skill\n')
    assert (r.s.checkout / path).read_bytes() == unchanged_checkout
    r.compact()
    value = observe(r)
    assert value['instructions'] == {'status': 'ready', 'changed': [path]}
    assert value['generation'] == {'before': 1, 'current': 2}
    continuation = action(value, 'instructions')
    assert re.search(r'\b(read|reread|refresh)\b', continuation)
    assert str(root / path) in continuation
    assert str(r.s.checkout / path) not in continuation


def test_changed_worktree_has_current_checkout_action(project, tmp_path, monkeypatch):
    r = Recovery(project, tmp_path, monkeypatch).enable()
    git(r, 'add', '--', 'src/billing/invoice.py')
    git(r, 'commit', '-m', 'Track the worktree action fixture')
    before = r.save()
    r.s.write('src/billing/invoice.py', b'VALUE = 7\n')
    assert git(r, 'diff', '--name-only') == 'src/billing/invoice.py'
    r.compact()
    value = observe(r)
    assert value['git']['head'] == before['captured']['git']['head']
    assert value['git']['changed_since_capture'] is True
    assert value['instructions'] == {'status': 'ready', 'changed': []}
    assert str(r.s.checkout) in action(value, 'git')


def test_changed_head_has_current_checkout_action(project, tmp_path, monkeypatch):
    r = Recovery(project, tmp_path, monkeypatch).enable()
    git(r, 'add', '--', 'src/billing/invoice.py')
    git(r, 'commit', '-m', 'Track the head action fixture')
    before = r.save()
    r.s.write('src/billing/invoice.py', b'VALUE = 8\n')
    git(r, 'add', '--', 'src/billing/invoice.py')
    git(r, 'commit', '-m', 'Change the isolated head fixture')
    expected = git(r, 'rev-parse', 'HEAD')
    assert expected != before['captured']['git']['head']
    r.compact()
    value = observe(r)
    assert value['git']['head'] == expected
    assert value['git']['changed_since_capture'] is True
    assert value['instructions'] == {'status': 'ready', 'changed': []}
    assert str(r.s.checkout) in action(value, 'git')


def test_changed_generation_has_current_reading_action(project, tmp_path, monkeypatch):
    r = Recovery(project, tmp_path, monkeypatch).enable()
    before = r.save()
    r.compact()
    value = observe(r)
    assert value['instructions'] == {'status': 'ready', 'changed': []}
    assert value['git']['head'] == before['captured']['git']['head']
    assert value['git']['changed_since_capture'] is False
    assert value['generation'] == {'before': 1, 'current': 2}
    continuation = action(value, 'coverage')
    assert str(r.s.db) in continuation
    assert re.search(r'\b(read|reread|refresh)\b', continuation)
    assert re.search(r'\b2\b', continuation)


def test_unchanged_resume_preserves_facts_without_change_actions(project, tmp_path, monkeypatch):
    r = Recovery(project, tmp_path, monkeypatch).enable()
    r.save()
    value = observe(r, 'resume')
    assert value['instructions'] == {'status': 'ready', 'changed': []}
    assert value['git']['changed_since_capture'] is False
    assert value['generation'] == {'before': 1, 'current': 1}
    assert not any(item.startswith(('restore:instructions;', 'restore:git;', 'restore:coverage;'))
                   for item in value['actions'])
    checkpoint = r.checkpoint_bytes()
    assert r.retry() == r.outputs[-1]
    assert r.checkpoint_bytes() == checkpoint
