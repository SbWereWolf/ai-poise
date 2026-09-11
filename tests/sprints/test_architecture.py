import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'src/poise'


def test_sprint_domain_and_application_do_not_import_infrastructure():
    paths=[ROOT/'modules/sprints/domain.py',ROOT/'application/sprints.py',ROOT/'modules/tasks/definition.py']
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            imports=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            assert not any(x.split('.')[0] in {'os','pathlib','sqlite3','subprocess','fcntl','time','datetime'} or 'infrastructure' in x for x in imports),(path,imports)


def test_runtime_does_not_write_sprint_tables():
    import re
    assert not re.search(r'(INSERT INTO|UPDATE|DELETE FROM) sprint_', (ROOT/'runtime.py').read_text())
