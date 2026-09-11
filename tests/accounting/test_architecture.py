import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'src/poise'

def test_accounting_domain_has_no_io_and_no_goal_dispatch():
    path=ROOT/'modules/accounting/domain.py';source=path.read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,ast.Import):modules=[n.name for n in node.names]
        elif isinstance(node,ast.ImportFrom):modules=[node.module or '']
        else:modules=[]
        assert not any(m.split('.')[0] in {'sqlite3','os','pathlib','subprocess','datetime','time'} for m in modules)
    assert "== 'development'" not in source

def test_accounting_does_not_change_task_lifecycle():
    for p in (ROOT/'infrastructure').rglob('*accounting*.py'):
        s=p.read_text()
        for forbidden in ('UPDATE tasks ','UPDATE task_execution ','UPDATE sprints '):assert forbidden not in s
