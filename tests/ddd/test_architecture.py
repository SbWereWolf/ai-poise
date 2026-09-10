import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src/harness"


def test_domain_has_no_io_imports_or_calls():
    forbidden = {"sqlite3", "subprocess", "pathlib", "os", "fcntl", "time", "datetime", "socket"}
    violations = []
    for path in (ROOT/"modules").rglob("*.py"):
        if path.name != "domain.py": continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else: modules = []
            for module in modules:
                if module.split(".")[0] in forbidden or "infrastructure" in module or "application" in module:
                    violations.append((str(path), module))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"open","eval","exec","__import__"}:
                violations.append((str(path), node.func.id))
    assert not violations, violations


def test_runtime_does_not_write_task_owned_tables_or_assign_lifecycle():
    source=(ROOT/"runtime.py").read_text()
    assert not re.search(r"(?:INSERT(?: OR \w+)? INTO|UPDATE|DELETE FROM) (?:tasks|submissions|section_layers|task_events|task_results|content_contracts|trace_point_layers|task_methods)\b",source)
    protected={"status","stage_index","iteration","claimed_by","history"}
    violations=[]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,(ast.Assign,ast.AugAssign,ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and target.value.id in {"data","task"}:
                    if isinstance(target.slice,ast.Constant) and target.slice.value in protected:
                        violations.append((node.lineno,target.slice.value))
    assert not violations, violations
