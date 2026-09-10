import ast
from pathlib import Path


def test_goal_definition_and_application_are_free_of_io():
    root=Path(__file__).resolve().parents[2]/"src/harness"
    files=list((root/"modules/goal_config").glob("*.py"))+[root/"application/goal_config.py"]
    assert files and all(p.exists() for p in files)
    for p in files:
        for n in ast.walk(ast.parse(p.read_text())):
            imports=([x.name for x in n.names] if isinstance(n,ast.Import) else [n.module or ""] if isinstance(n,ast.ImportFrom) else [])
            assert not any(x.split(".")[0] in {"os","pathlib","sqlite3","fcntl","subprocess","datetime","time"} or "infrastructure" in x for x in imports), (p,imports)


def test_runtime_and_editor_share_one_process_validator():
    root=Path(__file__).resolve().parents[2]/"src/harness"
    assert "GoalTypeDefinition" in (root/"common.py").read_text()
    assert "GoalTypeDefinition" in (root/"application/goal_config.py").read_text()
