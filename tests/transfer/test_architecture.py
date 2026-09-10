import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def test_english_rules_are_scoped_and_use_ensures_not_administers():
    import re
    root=(ROOT/'AGENTS.md').read_text();src=(ROOT/'src/AGENTS.md').read_text()
    assert not re.search('[А-Яа-яЁё]',root+src)
    assert not re.search(r'R-\d{3}',root+src)
    assert 'tool ensures' in src
    assert 'definition of done' in root and 'speculative' in root
    assert 'domain-driven' in src and 'migrations' in src


def test_transfer_domain_and_application_do_not_own_io():
    for name in ('modules/transfers/domain.py','application/transfers.py'):
        path=ROOT/'src/harness'/name
        source=path.read_text()
        for node in ast.walk(ast.parse(source)):
            imports=[x.name for x in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            assert not any(x.split('.')[0] in {'os','pathlib','sqlite3','subprocess','zipfile'} or 'infrastructure' in x for x in imports)
    assert "op=='transfer'" in (ROOT/'src/harness/application/work.py').read_text()
