"""Retained Bootstrap ownership guards, separate from new behavior RED."""

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_public_entrypoint_delegates_to_own_application():
    source = (ROOT / "bootstrap").read_text(encoding="utf-8")
    assert "from apps.bootstrap.application import main" in source
    assert "poise.runtime" not in source


def test_bootstrap_surface_excludes_engine_copy_and_legacy_route():
    source = (ROOT / "bootstrap").read_text(encoding="utf-8")
    for name in ("application.py", "contracts.py", "infrastructure.py", "environment_result.py"):
        source += (ROOT / "apps/bootstrap" / name).read_text(encoding="utf-8")
    for obsolete in ("requirements.json", "repairs.json", "--repairs", '"apply"'):
        assert obsolete not in source
    assert not (ROOT / "apps/bootstrap/catalogue.py").exists()
    assert not (ROOT / "apps/bootstrap/engine.py").exists()


def test_application_retains_own_distribution_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "ai-poise"
    assert isinstance(project["version"], str)
    assert project["version"].strip()
