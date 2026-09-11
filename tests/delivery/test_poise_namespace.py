from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = ROOT / "config" / "legacy-namespace-allowlist.json"
LEGACY_LOWER = "har" + "ness"
LEGACY_PATTERN = re.compile(LEGACY_LOWER, re.IGNORECASE)
IGNORED_PARTS = {".git", ".pytest_cache", "__pycache__"}
PROTECTED_PREFIXES = ("src/", "tests/", "tools/", "examples/", "skills/", "config/")
PROTECTED_FILES = {"AGENTS.md", "README.md", "pyproject.toml"}
APPROVED_HISTORICAL_PATTERNS = {
    f"delivery/task-definitions/{LEGACY_LOWER}/**",
    "delivery/requirements-bootstrap.json",
    "docs/architecture-design/**",
    "docs/reference/**",
    "docs/*step*.md",
    "docs/*pilot*.md",
    "docs/development-plan.md",
    "docs/plan-update-report.md",
    "docs/test-report.md",
}
APPROVED_MIGRATION_PATTERNS = {"docs/operations/replacement-upgrade.md"}


def _allowlist():
    assert ALLOWLIST.is_file(), (
        "config/legacy-namespace-allowlist.json must classify retained history"
    )
    data = json.loads(ALLOWLIST.read_text())
    assert set(data) == {"schema", "rules"}
    assert data["schema"] == "poise-legacy-namespace-allowlist-1"
    assert isinstance(data["rules"], list) and data["rules"]
    for rule in data["rules"]:
        assert set(rule) == {"path", "classification", "reason"}
        assert isinstance(rule["path"], str) and rule["path"]
        assert rule["classification"] in {"historical", "migration"}
        assert isinstance(rule["reason"], str) and rule["reason"].strip()
    return data["rules"]


def _files():
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in IGNORED_PARTS or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.is_file():
            yield path, relative.as_posix()


def _matches(relative: str, rules) -> bool:
    return any(fnmatch.fnmatchcase(relative, rule["path"]) for rule in rules)


def _legacy_occurrences():
    occurrences = []
    for path, relative in _files():
        if path == ALLOWLIST:
            continue
        if LEGACY_PATTERN.search(relative):
            occurrences.append((relative, "path"))
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            if LEGACY_PATTERN.search(line):
                occurrences.append((relative, str(number)))
    return occurrences


def test_live_namespace_has_no_unallowlisted_legacy_identifiers():
    rules = _allowlist()
    offenders = [f"{relative}:{location}" for relative, location in _legacy_occurrences()
                 if not _matches(relative, rules)]
    assert not offenders, "unallowlisted legacy namespace:\n" + "\n".join(offenders[:200])
    assert (ROOT / "src" / "poise").is_dir()
    assert not (ROOT / "src" / LEGACY_LOWER).exists()


def test_historical_allowlist_is_exact_used_and_non_executable():
    rules = _allowlist()
    occurrences = _legacy_occurrences()
    seen = set()
    for rule in rules:
        relative = rule["path"]
        approved = APPROVED_HISTORICAL_PATTERNS if rule["classification"] == "historical" \
            else APPROVED_MIGRATION_PATTERNS
        assert relative in approved
        assert relative not in PROTECTED_FILES
        assert not any(relative.startswith(prefix) for prefix in PROTECTED_PREFIXES)
        assert any(fnmatch.fnmatchcase(path, relative) for path, _ in occurrences), (
            f"stale allowlist rule: {relative}"
        )
        assert relative not in seen
        seen.add(relative)


def _historical_markdown(path: Path, rules) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return any(rule["classification"] == "historical"
               and fnmatch.fnmatchcase(relative, rule["path"]) for rule in rules)


def test_current_documentation_links_and_commands_use_poise():
    rules = _allowlist()
    required = (
        ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "src" / "AGENTS.md",
        ROOT / "skills" / "poise" / "SKILL.md",
        ROOT / "skills" / "poise-development" / "SKILL.md",
        ROOT / "docs" / "operations" / "replacement-upgrade.md",
    )
    assert all(path.is_file() for path in required)
    assert not (ROOT / "skills" / LEGACY_LOWER).exists()
    assert not (ROOT / "skills" / (LEGACY_LOWER + "-development")).exists()

    readme = (ROOT / "README.md").read_text()
    agents = (ROOT / "AGENTS.md").read_text()
    upgrade = (ROOT / "docs" / "operations" / "replacement-upgrade.md").read_text()
    assert ".venv/bin/poise --help" in readme
    assert "POISE_CONFIG" in readme and "POISE_SESSION" in readme
    assert "skills/poise/SKILL.md" in agents
    assert "tools/upgrade_to_ai_poise.py" in upgrade
    assert f"pip uninstall -y {('agent-' + LEGACY_LOWER + '-happy-path')}" in upgrade
    assert "ai-poise" in upgrade and "poise --help" in upgrade

    link_pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    broken = []
    for document, relative in _files():
        if document.suffix.lower() != ".md" or _historical_markdown(document, rules):
            continue
        for raw in link_pattern.findall(document.read_text()):
            target = raw.strip().strip("<>")
            if not target or target.startswith(("#", "/", "http://", "https://", "mailto:", "codex:")):
                continue
            target_path = unquote(target.split("#", 1)[0])
            if target_path and not (document.parent / target_path).resolve().exists():
                broken.append(f"{relative} -> {target}")
    assert not broken, "broken current documentation links:\n" + "\n".join(broken)


def test_live_project_uses_allocator_owned_task_identity():
    project = json.loads(
        (ROOT / "config" / "projects" / "ai-poise" / "project.json").read_text()
    )
    policy = project.get("task_ids")
    assert isinstance(policy, dict), (
        "the live project must publish an explicit task_ids policy through its owning API"
    )
    assert set(policy) == {"namespace", "width", "progression"}
    assert set(policy["namespace"]) == {"minimum", "maximum"}
    assert set(policy["progression"]) == {"first", "step"}

    workflow = (ROOT / "docs" / "workflows" / "batch-work.md").read_text()
    boundaries = (ROOT / "docs" / "architecture" / "boundaries.md").read_text()
    assert "agent при обычном создании нового Task использует automatic" in workflow
    assert "никогда не выбирает номер сам" in workflow
    assert "TaskRepository.allocate" in boundaries
    assert "не вычисляют номер" in boundaries
