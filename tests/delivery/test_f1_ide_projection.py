"""C043 reuses the existing IDE policy; fixtures are not live IDE evidence."""
from pathlib import Path
from poise.infrastructure.development_routing import load_development_routing

ROOT = Path(__file__).resolve().parents[2]


def test_existing_skill_links_to_real_navigation_and_router_sections():
    text=(ROOT/'.agents/skills/poise-development/SKILL.md').read_text()
    assert 'code-navigation.md#конкретная-папка-и-подтверждение-индекса' in text
    assert 'development-routing.md#подключение-к-bootstrap-и-обновлению-контекста' in text
    assert 'markdown-formatting.md#явное-форматирование-при-недоступной-ide' in text
    assert 'not live IDE evidence' in text
    assert 'Work in a dedicated `tasks/<task-id>` worktree.' not in text
    assert 'worktree_required' in text


def test_canonical_policy_covers_copy_without_dedicated_worktree_and_actual_owners():
    doc=(ROOT/'docs/governance/jetbrains-mcp-policy.md').read_text()
    assert '## Применение поставленных инструментов F1' in doc
    assert 'без dedicated worktree' in doc
    assert 'DevelopmentRouting' in doc and 'CodeNavigation' in doc
    assert 'поддерживающее evidence' in doc
    assert not (ROOT/'.agents/skills/jetbrains-ide').exists()


def test_router_selects_existing_skill_without_creating_an_ide_profile():
    router=load_development_routing(catalog=ROOT/'.agents/skill-catalog.json',
        selection=ROOT/'config/development/skill-selection.json',policy=ROOT/'config/development/routing.json',
        packages=ROOT/'config/testing/test-packages.json')
    result=router.route({'checkout':str(ROOT),'stage':'code_review','handler':'inspect',
        'scope_paths':['src/poise/modules/skills/**'],'changed_paths':[], 'facts':[],
        'required_methods':[],'registered_methods':[],'result_contract':{'sections':['review']}})
    assert result['skills'].count('poise-development')==1
    assert 'review' in result['skills']
    assert not any('jetbrains' in s for s in result['skills'])


def test_agent_projection_points_to_the_same_normative_owner():
    rules=(ROOT/'AGENTS.md').read_text()
    assert 'jetbrains-mcp-policy.md#применение-поставленных-инструментов-f1' in rules
