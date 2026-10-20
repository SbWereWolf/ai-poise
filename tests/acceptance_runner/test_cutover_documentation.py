"""C08 late documentation assertions, never product RED during test writing."""
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_environment_skill_lands_next_to_the_setup_instruction():
    skill = (ROOT / '.agents/skills/poise/SKILL.md').read_text()
    assert 'poise-command-reference.md#обслуживание-окружения' in skill
    lines = (ROOT / 'docs/operator/poise-command-reference.md').read_text().splitlines()
    anchor = lines.index('## Обслуживание окружения')
    setup = 'maintenance-profile-v2.md#развёртывание-из-примера'
    distances = [index - anchor for index, line in enumerate(lines) if index > anchor and setup in line]
    assert distances and min(distances) <= 10


def test_operator_working_settings_are_ignored_and_untracked():
    paths = json.loads((ROOT / 'tests/maintenance/fixtures/p005/working-paths.json').read_text())
    tracked = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '--', *paths],
                             capture_output=True, text=True)
    assert (tracked.returncode, tracked.stdout, tracked.stderr) == (0, '', '')
    ignored = subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '--', *paths],
                             capture_output=True, text=True)
    assert ignored.returncode == 0 and ignored.stderr == ''
    assert ignored.stdout.splitlines() == paths
