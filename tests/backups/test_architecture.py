from __future__ import annotations

import ast
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_backup_layers_keep_sqlite_and_filesystem_out_of_domain_and_work():
    domain = ROOT / "src/poise/modules/backups/domain.py"
    ports = ROOT / "src/poise/modules/backups/ports.py"
    application = ROOT / "src/poise/application/backups.py"
    infrastructure = ROOT / "src/poise/infrastructure/backups.py"
    interface = ROOT / "src/poise/interfaces/backups.py"
    for path in (domain, ports, application, infrastructure, interface):
        assert path.is_file(), f"missing planned backup layer: {path.relative_to(ROOT)}"

    forbidden = {"sqlite3", "os", "shutil", "pathlib"}
    assert imports(domain).isdisjoint(forbidden)
    assert imports(application).isdisjoint(forbidden)
    assert "sqlite3" in imports(infrastructure)
    assert "backups" not in (ROOT / "src/poise/application/work.py").read_text(encoding="utf-8")
    assert "backups" not in (ROOT / "src/poise/modules/tasks/domain.py").read_text(encoding="utf-8")
    assert "backups" not in (ROOT / "src/poise/modules/sprints/domain.py").read_text(encoding="utf-8")


def test_package_installs_the_public_poise_command():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["name"] == "ai-poise"
    assert project["scripts"] == {"poise": "poise.__main__:main"}
