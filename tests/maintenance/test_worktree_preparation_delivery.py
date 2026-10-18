"""Late delivery checks have distinct implementation/documentation schedules."""
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = 'docs/operator/worktree-preparation.md'
ANCHOR = '#подготовка-рабочей-области'


def test_sanitized_example_and_ignored_working_configuration():
    example = ROOT / 'config/projects/erp/worktree-preparation.example.json'
    configured = json.loads(example.read_text())
    assert {'parameters', 'catalog_dir', 'inputs', 'operations', 'commands', 'handler'} <= set(configured)
    assert configured['operations'] == ['infra', 'deps', 'check']
    assert configured['inputs']
    assert isinstance(configured['handler']['callable'], str)
    versioned = subprocess.run(['git', 'ls-files', '--error-unmatch', '--', str(example.relative_to(ROOT))],
                               cwd=ROOT, capture_output=True, text=True)
    assert versioned.returncode == 0
    example_ignored = subprocess.run(['git', 'check-ignore', '--no-index', str(example.relative_to(ROOT))],
                                     cwd=ROOT, capture_output=True, text=True)
    assert example_ignored.returncode == 1
    ignored = subprocess.run(['git', 'check-ignore', '--no-index', 'operator/worktree-preparation.json'],
                             cwd=ROOT, capture_output=True, text=True)
    assert ignored.returncode == 0
    tracked = subprocess.check_output(['git', 'ls-files', '--', 'operator/worktree-preparation.json'], cwd=ROOT)
    assert tracked == b''
    # Sanitized examples cannot contain this installation's deployment paths.
    assert '/home/sbwerewolf/' not in example.read_text()


def test_operator_instruction_declares_inputs_results_and_retry():
    text = (ROOT / DOCUMENT).read_text()
    assert '# Подготовка рабочей области' in text
    for term in ('worktree_preparation', 'configuration', 'parameters', 'catalog_dir',
                 'inputs', 'source', 'destination', 'operations', 'commands', 'handler',
                 'preparation_request_id', 'receipt_path', 'receipt_sha256', 'unconfirmed'):
        assert term in text, term
    assert 'worktree-preparation.example.json' in text
    reference = (ROOT / 'docs/operator/poise-command-reference.md').read_text()
    assert 'worktree-preparation.md' + ANCHOR in reference


def test_skill_links_exact_instruction_within_ten_lines_of_heading():
    lines = (ROOT / '.agents/skills/poise/SKILL.md').read_text().splitlines()
    matches = [i for i, line in enumerate(lines) if 'worktree-preparation.md' + ANCHOR in line]
    assert matches, 'Poise skill must link directly to the canonical instruction'
    for index in matches:
        headings = [i for i in range(index + 1) if lines[i].startswith('#')]
        assert headings and index - headings[-1] <= 10


def test_changed_documentation_uses_existing_checker():
    checked = subprocess.run([sys.executable, '-B', 'tools/check_documentation.py',
                              '--checkout', str(ROOT), '--changed', DOCUMENT,
                              '--changed', 'docs/operator/poise-command-reference.md',
                              '--changed', '.agents/skills/poise/SKILL.md'],
                             cwd=ROOT, capture_output=True, text=True)
    assert checked.returncode == 0, (checked.stdout, checked.stderr)
