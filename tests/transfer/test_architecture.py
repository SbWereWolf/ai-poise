import ast
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def agent_facing_markdown(source):
    """Exclude link destinations while retaining their human-visible labels."""
    return re.sub(r'(?<=\])\([^()\n]*\)', '', source)


def test_english_rules_are_scoped_and_use_ensures_not_administers():
    root=(ROOT/'AGENTS.md').read_text();src=(ROOT/'src/AGENTS.md').read_text()
    assert not re.search('[А-Яа-яЁё]',agent_facing_markdown(root+src))
    assert not re.search(r'R-\d{3}',root+src)
    assert 'tool ensures' in src
    assert 'definition of done' in root and 'speculative' in root
    assert 'domain-driven' in src and 'migrations' in src


def test_language_scope_ignores_unicode_anchor_but_keeps_visible_link_text():
    assert not re.search('[А-Яа-яЁё]',agent_facing_markdown('[Rules](docs/rules.md#общие-правила)'))
    assert re.search('[А-Яа-яЁё]',agent_facing_markdown('[Правила](docs/rules.md#rules)'))


def test_transfer_domain_and_application_do_not_own_io():
    for name in ('modules/transfers/domain.py','application/transfers.py'):
        path=ROOT/'src/poise'/name
        source=path.read_text()
        for node in ast.walk(ast.parse(source)):
            imports=[x.name for x in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            assert not any(x.split('.')[0] in {'os','pathlib','sqlite3','subprocess','zipfile'} or 'infrastructure' in x for x in imports)
    assert "op=='transfer'" in (ROOT/'src/poise/application/work.py').read_text()
