import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'src/harness'


def test_catalogue_domain_application_use_ports_and_owners_not_io():
    paths=[ROOT/'modules/catalogue/domain.py',ROOT/'modules/catalogue/publication.py',
           ROOT/'application/catalogue.py',ROOT/'application/planning_publication.py']
    for path in paths:
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            imports=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            assert not any(x.split('.')[0] in {'os','pathlib','sqlite3','subprocess','fcntl','time','datetime'} or 'infrastructure' in x for x in imports),(path,imports)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                assert node.func.id not in {'open','exec','eval'}


def test_publication_adapter_and_catalogue_never_write_task_tables_directly():
    import re
    for name in ('infrastructure/catalogue.py','application/planning_publication.py','infrastructure/actions.py'):
        text=(ROOT/name).read_text()
        assert not re.search(r'(INSERT INTO|UPDATE|DELETE FROM) (tasks|sprints|submissions|section_layers)\b',text)
