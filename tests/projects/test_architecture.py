import ast
from pathlib import Path


def test_project_domain_and_application_depend_on_ports_not_io():
    root=Path(__file__).resolve().parents[2]/'src/poise'
    paths=list((root/'modules/projects').glob('*.py'))+[root/'application/projects.py']
    assert len(paths)>=3
    for path in paths:
        for n in ast.walk(ast.parse(path.read_text())):
            imports=([a.name for a in n.names] if isinstance(n,ast.Import) else [n.module or ''] if isinstance(n,ast.ImportFrom) else [])
            assert not any(x.split('.')[0] in {'os','pathlib','sqlite3','subprocess','fcntl','time','datetime'} or 'infrastructure' in x for x in imports),(path,imports)


def test_setup_validates_with_runtime_validator_and_does_not_write_task_state():
    root=Path(__file__).resolve().parents[2]/'src/poise'
    text=(root/'infrastructure/projects.py').read_text()
    assert 'load_config(' in text
    assert 'INSERT INTO' not in text and 'UPDATE tasks' not in text
