"""C005.1: shared, explicit skill metadata; no instruction-body loading."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def catalog_data():
    return {
        "schema": "poise-skill-catalog-1",
        "skills": [
            {"id": "workflow", "path": ".agents/skills/workflow/SKILL.md",
             "purpose": "Coordinate a task.", "class": "meta", "level": None,
             "responsibility": None},
            {"id": "testing", "path": ".agents/skills/testing/SKILL.md",
             "purpose": "Design independent assertions.", "class": "common", "level": None,
             "responsibility": None},
            {"id": "backend", "path": ".agents/skills/backend/SKILL.md",
             "purpose": "Implement backend behavior.", "class": "working", "level": 1,
             "responsibility": "backend"},
        ],
    }


def owner():
    assert importlib.util.find_spec("poise.modules.skills") is not None, "C005.1 catalog owner is missing"
    from poise.modules.skills.catalog import SkillCatalog
    return SkillCatalog


def test_catalog_preserves_metadata_and_is_order_independent():
    raw = catalog_data()
    catalog = owner().parse(raw)
    expected = deepcopy(sorted(raw["skills"], key=lambda s: s["id"]))
    assert catalog.as_dict() == {"schema": raw["schema"], "skills": expected}
    raw["skills"].reverse()
    assert owner().parse(raw).as_dict() == catalog.as_dict()
    raw["skills"][0]["purpose"] = "Caller mutation"
    output = catalog.as_dict()
    output["skills"][0]["purpose"] = "Output mutation"
    assert catalog.as_dict()["skills"] == expected


def test_catalog_selects_exact_ids_and_supplies_existing_policy_projection():
    catalog = owner().parse(catalog_data())
    assert [s["id"] for s in catalog.select(["workflow", "backend"])] == ["backend", "workflow"]
    assert catalog.select([]) == []
    assert catalog.decomposition_skills(["testing", "backend", "workflow"]) == [
        {"id": "backend", "class": "narrow", "responsibility": "backend"},
        {"id": "testing", "class": "general", "responsibility": None},
        {"id": "workflow", "class": "meta", "responsibility": None},
    ]
    from poise.modules.tasks.decomposition import FocusedDecomposition
    policy = {"skills": catalog.decomposition_skills(["backend", "workflow"]),
              "areas": [{"path": "app/**", "responsibility": "backend"}]}
    assert FocusedDecomposition.validate_policy(policy)["skills"]["backend"] == ("narrow", "backend")


@pytest.mark.parametrize("ids", [["missing"], ["workflow", "workflow"], "workflow", [1], [""]])
def test_selection_rejects_invalid_or_unknown_ids(ids):
    from poise.modules.foundation.errors import DomainError
    with pytest.raises(DomainError):
        owner().parse(catalog_data()).select(ids)


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", "a/b"), ("path", "../private/SKILL.md"),
    ("path", "/private/SKILL.md"), ("path", "C:/private/SKILL.md"),
    ("path", "a\\SKILL.md"), ("path", "a/./SKILL.md"), ("path", "a//SKILL.md"),
    ("path", "a/*/SKILL.md"), ("path", "a/SKILL.md\x00"), ("purpose", "  "),
    ("class", "general"), ("level", None), ("level", True), ("level", 0),
    ("responsibility", None), ("responsibility", "  "),
])
def test_catalog_rejects_invalid_working_metadata(field, value):
    from poise.modules.foundation.errors import DomainError
    raw = catalog_data()
    raw["skills"][2][field] = value
    with pytest.raises(DomainError):
        owner().parse(raw)


def test_catalog_rejects_duplicate_ids_paths_missing_and_extra_fields():
    from poise.modules.foundation.errors import DomainError
    variants = []
    duplicate_id = catalog_data()
    duplicate_id["skills"][1]["id"] = "workflow"
    variants.append(duplicate_id)
    duplicate_path = catalog_data()
    duplicate_path["skills"][1]["path"] = duplicate_path["skills"][0]["path"]
    variants.append(duplicate_path)
    missing = catalog_data()
    del missing["skills"][1]["purpose"]
    variants.append(missing)
    extra = catalog_data()
    extra["skills"][1]["content"] = "Do not duplicate a skill body."
    variants.extend([extra, {"schema": "other", "skills": []}, {"schema": "poise-skill-catalog-1", "skills": []}])
    for raw in variants:
        with pytest.raises(DomainError):
            owner().parse(raw)


@pytest.mark.parametrize("field,value", [("level", 1), ("responsibility", "backend")])
def test_meta_and_common_do_not_claim_narrow_responsibility(field, value):
    from poise.modules.foundation.errors import DomainError
    for index in (0, 1):
        raw = catalog_data()
        raw["skills"][index][field] = value
        with pytest.raises(DomainError):
            owner().parse(raw)


def fixture_catalog(tmp_path):
    raw = catalog_data()
    for skill in raw["skills"]:
        p = tmp_path / skill["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("INSTRUCTION BODY MUST NOT BE READ", encoding="utf-8")
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_loader_checks_paths_without_reading_instruction_bodies(tmp_path, monkeypatch):
    owner()
    from poise.infrastructure.skill_catalog import load_skill_catalog
    path = fixture_catalog(tmp_path)
    original = Path.read_text
    def bounded_read(self, *args, **kwargs):
        assert self == path, "Catalog loading must not read skill or reference bodies"
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", bounded_read)
    catalog = load_skill_catalog(path, tmp_path)
    assert len(catalog.as_dict()["skills"]) == 3


def test_loader_rejects_missing_or_escaped_instruction_file(tmp_path):
    owner()
    from poise.infrastructure.skill_catalog import load_skill_catalog
    from poise.modules.foundation.errors import PoiseError
    path = fixture_catalog(tmp_path)
    skill = tmp_path / ".agents/skills/backend/SKILL.md"
    skill.unlink()
    with pytest.raises(PoiseError, match="backend"):
        load_skill_catalog(path, tmp_path)
    foreign = tmp_path.parent / (tmp_path.name + "-outside.md")
    foreign.write_text("outside")
    skill.symlink_to(foreign)
    with pytest.raises(PoiseError, match="backend"):
        load_skill_catalog(path, tmp_path)


def test_loader_rejects_duplicate_json_keys(tmp_path):
    owner()
    from poise.infrastructure.skill_catalog import load_skill_catalog
    from poise.modules.foundation.errors import PoiseError
    path = fixture_catalog(tmp_path)
    path.write_text('{"schema":"poise-skill-catalog-1","schema":"poise-skill-catalog-1","skills":[]}')
    with pytest.raises(PoiseError):
        load_skill_catalog(path, tmp_path)


def test_distributed_catalog_matches_only_delivered_skills():
    owner()
    from poise.infrastructure.skill_catalog import load_skill_catalog
    catalog = load_skill_catalog(ROOT / ".agents/skill-catalog.json", ROOT)
    entries = catalog.as_dict()["skills"]
    actual = {p.parent.name for p in (ROOT / ".agents/skills").glob("*/SKILL.md")}
    assert {item["id"] for item in entries} == actual
    assert len(actual) == 35
    assert not {"safe-commit", "system-tests", "preflight", "playwright", "jetbrains-ide"} & actual
    assert all(item["path"] == f".agents/skills/{item['id']}/SKILL.md" for item in entries)
    assert {item["class"] for item in entries} == {"meta", "common", "working"}


def test_catalog_cli_is_read_only_and_does_not_need_harness(tmp_path):
    path = fixture_catalog(tmp_path)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(
        [sys.executable, "-B", "-m", "poise", "skills", "--catalog", str(path), "--root", str(tmp_path)],
        input=json.dumps({"operation": "read", "skill_ids": ["workflow", "backend"]}),
        text=True, capture_output=True,
        env={"PATH": str(Path(sys.executable).parent), "PYTHONPATH": str(ROOT / "src")},
        cwd=tmp_path, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert [s["id"] for s in json.loads(result.stdout)["skills"]] == ["backend", "workflow"]
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
