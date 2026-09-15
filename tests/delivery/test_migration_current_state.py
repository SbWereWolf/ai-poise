import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_canonical_retrospective_points_to_current_verified_state():
    text = (ROOT / 'docs/migrations/erp-runtime-migration-retrospective-2026-09-15.md').read_text()
    assert 'current-state.md#состояние-после-восстановления' in text
    assert 'sprint-dependency-closure-audit.md' in text
    assert 'заменяет ранний вариант' in text


def test_recorded_snapshot_has_closed_acyclic_graphs_and_unique_local_ids():
    snapshot = json.loads((ROOT / 'docs/migrations/sprint-dependency-closure-2026-09-15.json').read_text())
    assert snapshot['result'] == 'PASS'
    assert snapshot['errors'] == []
    all_members = set()
    for sprint in snapshot['results'].values():
        members = set(sprint['tasks'])
        assert not (members & all_members)
        all_members |= members
        assert members == {node for edge in sprint['edges'] for node in edge}
        positions = {task: n for n, task in enumerate(sprint['topological_order'])}
        assert set(positions) == members
        for before, after in sprint['edges']:
            assert positions[before] < positions[after]
        assert sprint['dangling'] == sprint['isolated'] == sprint['cycles'] == []
    for task in snapshot['standalone']:
        assert task['sprint_id'] is None
        assert task['task'] not in all_members
    for duplicate in snapshot['conditional_duplicates']:
        assert duplicate['contract_present'] is True
        assert duplicate['task'] in all_members


def test_state_distinguishes_code_and_lifecycle_and_does_not_hide_pending_placement():
    text = (ROOT / 'docs/migrations/current-state.md').read_text()
    assert 'не входит в ancestry' in text
    assert '**ещё не реализовано**' in text
    assert 'completed' in text and 'newborn' in text
    assert '3434944' not in text or 'C018' in text
