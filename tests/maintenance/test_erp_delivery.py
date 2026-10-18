"""Late delivery checks: assets at implementation, documentation at its stage."""
import sys

import pytest
pytest.register_assert_rewrite('erp_support')
from erp_support import CONTRACT, REPO, SPEC, invoke, read_json


def test_erp_examples_and_referenced_documents_are_versioned():
    specification = read_json(REPO / SPEC)
    required = [SPEC, specification['catalog'], specification['recommendations']]
    required += [item['source'] for item in specification['documents']]
    for relative in required:
        path = REPO / relative
        assert path.is_file(), relative
        assert 'example' in path.name, 'Ship populated examples, not deployment configuration'
        result = invoke(['git', 'ls-files', '--error-unmatch', '--', relative])
        assert result.returncode == 0, (relative, result.stdout, result.stderr)
        assert '/home/sbwerewolf/' not in path.read_text(), 'No installation-specific local root'


def test_working_erp_profile_is_ignored_and_examples_are_not():
    for name in CONTRACT['working_profile_files']:
        relative = 'operator/profile/' + name
        result = invoke(['git', 'check-ignore', '--no-index', '--', relative])
        assert result.returncode == 0, relative
        tracked = invoke(['git', 'ls-files', '--', relative])
        assert tracked.returncode == 0 and tracked.stdout == ''
    result = invoke(['git', 'check-ignore', '--no-index', '--', str(SPEC)])
    assert result.returncode == 1, 'Source examples remain versionable'


def test_erp_operator_instruction_describes_selected_contract_and_limits():
    path = REPO / 'docs/operator/maintenance-profile-v2.md'
    content = path.read_text()
    assert '## Профиль ERP' in content
    section = content.split('## Профиль ERP', 1)[1]
    for literal in ('--profile-spec', str(SPEC), 'operator/profile', 'infra', 'deps',
                    'check', 'configuration_observations', 'string.Template', '.env'):
        assert literal in section, literal
    for parameter in CONTRACT['configured_parameters']:
        assert parameter in section, parameter
    for group in CONTRACT['groups']:
        assert group in section, 'Document the meaning of every catalogue group'


def test_poise_skill_links_exact_erp_instruction_within_ten_lines():
    path = REPO / '.agents/skills/poise/SKILL.md'
    lines = path.read_text().splitlines()
    target = 'docs/operator/maintenance-profile-v2.md#профиль-erp'
    links = [index for index, line in enumerate(lines) if target in line]
    assert links, 'The operating skill must expose the canonical ERP instruction'
    assert any(any(line.startswith('#') for line in lines[max(0, index - 10):index + 1])
               for index in links), 'Instruction link is at most ten lines from its skill anchor'


def test_existing_documentation_owner_accepts_changed_instruction_and_skill():
    result = invoke([sys.executable, '-B', REPO / 'tools/check_documentation.py',
                     '--checkout', REPO, '--changed', 'docs/operator/maintenance-profile-v2.md',
                     '--changed', '.agents/skills/poise/SKILL.md'])
    assert result.returncode == 0, (result.stdout, result.stderr)
