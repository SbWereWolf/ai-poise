"""Task 0067: retired seed cannot return as executable or canonical input."""
from pathlib import Path
import json
import pytest

ROOT = Path(__file__).resolve().parents[2]
RETIRED = (
    "tools/seed_wsl_tasks.py",
    *(f"delivery/task-definitions/{'har' + 'ness'}/{n:04d}.json" for n in (1, 2, 3)),
    "delivery/requirements-bootstrap.json",
    "tests/delivery/test_wsl_seed.py",
    "tests/delivery/test_baseline_seed_publication.py",
)

@pytest.mark.parametrize("relative", RETIRED)
def test_retired_seed_files_are_absent(relative):
    assert not (ROOT / relative).exists(), relative

@pytest.mark.parametrize("relative", (
    "README.md", "delivery/README.md", "docs/configuration/wsl-local-delivery.md",
    "docs/workflows/requirements-registry.md",
))
def test_current_instructions_do_not_reference_retired_seed(relative):
    text = (ROOT / relative).read_text()
    for retired in RETIRED:
        assert retired not in text, f"{relative}: {retired}"
    assert "seed_wsl_tasks" not in text


def test_delivery_does_not_ship_a_second_requirements_store():
    assert not list((ROOT / "delivery").rglob("*.json"))
    text = (ROOT / "delivery/README.md").read_text()
    assert "Requirements DB" in text and "6" in text and "4" in text


def test_single_wsl_template_has_project_local_storage_and_no_system_split():
    blueprint = json.loads((ROOT / "config/project-templates/wsl-poise.json").read_text())
    paths = blueprint["config"]["paths"]
    assert paths["database"] == "tasks.sqlite"
    assert paths["standalone_tasks"] == "artifacts/standalone"
    assert paths["sprints"] == "artifacts/sprints"
    assert not (ROOT / "config/project-templates/wsl-system.json").exists()
    assert not (ROOT / "delivery/task-definitions/system").exists()
