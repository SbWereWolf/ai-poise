"""Real Git and public Poise commit producers; no manual hook invocation."""
import json
from pathlib import Path
import subprocess

import pytest

from tests.conftest import git
from .helpers import FIXTURES, SOURCE, Scene, phases


def add_delivery(root):
    (root / 'src/delivery.py').write_bytes((FIXTURES / 'delivery.py').read_bytes())


def trace(scene):
    return [json.loads(line) for line in scene.hook_trace.read_text().splitlines()]


def assert_callback(scene, root, head):
    records = trace(scene)
    selected = [row for row in records if row['head'] == head and row['root'] == str(root)]
    assert [row['position'] for row in selected] == ['foreign-before', 'index', 'foreign-after']
    assert selected[1]['exit_code'] == 0
    assert selected[1]['complete_captures']
    index = json.loads(selected[1]['stdout'])
    assert index['checkout'] == str(root)
    assert index['database'] == str(root / '.runtime/ast-index/index.db')
    assert index['status'] == 'ready'
    assert any(row['operation'] == 'update' and row['phase'] == 'entered'
               and row['cwd'] == row['root'] == str(root)
               and row['database'] == str(root / '.runtime/ast-index/index.db')
               for row in scene.records())


@pytest.mark.parametrize('producer', ['ordinary', 'merge', 'verification', 'checkpoint', 'integration'])
def test_successful_commit_delivers_real_refresh(project, tmp_path, monkeypatch, producer):
    scene = Scene(project, tmp_path, connected=producer in ('verification', 'checkpoint', 'integration'))
    scene.require(); scene.ready(); scene.hooks()
    monkeypatch.setenv('PYTHONPATH', str(SOURCE / 'src'))
    root = scene.checkout
    if producer == 'ordinary':
        add_delivery(root); git(root, 'add', 'src/delivery.py')
        git(root, 'commit', '-m', 'Shipping imports the invoice total')
        head = git(root, 'rev-parse', 'HEAD')
    elif producer == 'merge':
        git(root, 'checkout', '-b', 'shipping-import')
        add_delivery(root); git(root, 'add', 'src/delivery.py')
        git(root, 'commit', '-m', 'Add shipping on source branch')
        git(root, 'checkout', 'main')
        git(root, 'merge', '--no-ff', '-m', 'Bring shipping into main', 'shipping-import')
        head = git(root, 'rev-parse', 'HEAD')
        assert len(git(root, 'show', '-s', '--format=%P', head).split()) == 2
    elif producer == 'checkpoint':
        from tests.actions.helpers import setup, result, verify
        runtime, context, plan, _ = setup(project, conflict=False)
        root = Path(context['worktree'])
        (root / '.ast-index.yaml').write_bytes((FIXTURES / 'index.yaml').read_bytes())
        code, _ = scene.invoke('rebuild', checkout=root); assert code == 0
        actual = verify(runtime, result(context, {
            'plan': plan, 'phase': 'prepare', 'resolutions': [], 'finding_resolutions': []}))
        assert actual['status'] == 'verified', actual
        head = git(root, 'log', '-1', '--format=%H', '--grep=^WIP integration left$')
        assert head
        assert len(git(root, 'show', '-s', '--format=%P', head).split()) == 2
    else:
        from tests.result_integration.helpers import prepare_completed_task, integration_input, request

        def change(target):
            (target / '.ast-index.yaml').write_bytes((FIXTURES / 'index.yaml').read_bytes())
            code, _ = scene.invoke('rebuild', checkout=target); assert code == 0
            add_delivery(target)

        tools, root, source = prepare_completed_task(project, change, accept=producer == 'integration')
        if producer == 'verification':
            head = source
        else:
            # Force the candidate owner to make its own merge commit, rather than no-op.
            (scene.checkout / 'README.md').write_text('Independent target advance\n')
            git(scene.checkout, 'add', 'README.md')
            git(scene.checkout, 'commit', '-m', 'Document the independently advanced target')
            target = git(scene.checkout, 'rev-parse', 'HEAD')
            completed = tools.invoke(request('integrate', integration_input(project, source)))
            assert completed['status'] == 'integrated', completed
            head = git(scene.checkout, 'rev-parse', 'HEAD')
            assert head not in (source, target)
            assert len(git(scene.checkout, 'show', '-s', '--format=%P', head).split()) == 2
    assert_callback(scene, root, head)
    if root.exists():
        code, result = scene.query(checkout=root)
        assert (code, result['status']) == (0, 'complete')


def test_existing_hook_order_and_worktree_root_are_preserved(project, tmp_path, monkeypatch):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    sibling = scene.sibling()
    code, _ = scene.invoke('rebuild', checkout=sibling); assert code == 0
    example_bytes = (SOURCE / 'config/examples/source-index-post-commit.example.sh').read_bytes()
    example = scene.parent / 'source-index-post-commit.sh'
    example.write_bytes(example_bytes)
    directory = scene.hooks(example=example)
    monkeypatch.setenv('PYTHONPATH', str(SOURCE / 'src'))
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    configured = git(scene.checkout, 'config', '--get', 'core.hooksPath')
    for root in (scene.checkout, sibling):
        (root / 'src/new.py').write_text('def independent(): return 9\n')
        git(root, 'add', 'src/new.py')
        # A real commit from a subdirectory must still select the Git root.
        git(root / 'src', 'commit', '-m', 'Preserve the selected worktree identity')
        assert_callback(scene, root, git(root, 'rev-parse', 'HEAD'))
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert example.read_bytes() == example_bytes
    assert git(scene.checkout, 'config', '--get', 'core.hooksPath') == configured
    assert (sibling / '.runtime/ast-index/index.db').is_file()
    assert scene.database.is_file()


def test_rejected_commit_does_not_refresh(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    directory = scene.hooks()
    veto = directory / 'pre-commit'; veto.write_text('#!/usr/bin/env bash\nexit 8\n'); veto.chmod(0o755)
    before = git(scene.checkout, 'rev-parse', 'HEAD')
    add_delivery(scene.checkout); git(scene.checkout, 'add', 'src/delivery.py')
    cp = subprocess.run(['git', 'commit', '-m', 'Rejected source commit'], cwd=scene.checkout,
                        env=scene.environment, text=True, capture_output=True)
    assert cp.returncode != 0
    assert git(scene.checkout, 'rev-parse', 'HEAD') == before
    assert 'update' not in phases(scene)
    assert not scene.hook_trace.exists()


def test_refresh_failure_does_not_erase_successful_commit(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready(); scene.hooks()
    scene.controls['update_exit'] = 7; scene.save()
    before = git(scene.checkout, 'rev-parse', 'HEAD')
    add_delivery(scene.checkout); git(scene.checkout, 'add', 'src/delivery.py')
    cp = subprocess.run(['git', 'commit', '-m', 'Commit survives index notification failure'],
                        cwd=scene.checkout, env=scene.environment, text=True, capture_output=True)
    assert cp.returncode == 0, cp.stderr
    head = git(scene.checkout, 'rev-parse', 'HEAD'); assert head != before
    assert git(scene.checkout, 'show', head + ':src/delivery.py') == (FIXTURES / 'delivery.py').read_text().rstrip()
    row = next(row for row in trace(scene) if row['position'] == 'index')
    assert row['head'] == head and row['exit_code'] == 1
    assert json.loads(row['stdout'])['recovery']
    code, result = scene.query()
    assert code == 1 and result['status'] == 'failed'
    assert 'usages' not in phases(scene)
