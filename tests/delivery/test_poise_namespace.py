from __future__ import annotations

import ast
import fnmatch
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = ROOT / "config" / "legacy-namespace-allowlist.json"
LEGACY_LOWER = "har" + "ness"
LEGACY_PATTERN = re.compile(LEGACY_LOWER, re.IGNORECASE)
IGNORED_PARTS = {".git", ".pytest_cache", "__pycache__", ".poise-test-cache"}
PROTECTED_PREFIXES = ("src/", "tests/", "tools/", "examples/", "skills/", "config/")
PROTECTED_FILES = {"AGENTS.md", "README.md", "pyproject.toml"}
APPROVED_HISTORICAL_PATTERNS = {
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


LIVE_DIRECTORIES = (
    "src", "tests", "tools", "examples", "skills", "config", "docs", "delivery",
    "recovery-tools", ".agents", ".codex",
)


def _files(root: Path = ROOT):
    """Audit shipped surfaces, including untracked code, not mutable Task data."""
    for name in sorted(PROTECTED_FILES):
        path = root / name
        if path.is_file():
            yield path, name
    for name in LIVE_DIRECTORIES:
        for path in (root / name).rglob("*"):
            relative = path.relative_to(root)
            if any(part in IGNORED_PARTS or part.endswith(".egg-info") for part in relative.parts):
                continue
            if path.is_file():
                yield path, relative.as_posix()


# These are supported namespace surfaces, not a ban on the ordinary noun or on
# immutable Task/installation IDs. Python fixture literals are not entry points.
_IDENTIFIER = re.compile(
    rf"^(?:{LEGACY_LOWER}(?:_|$)|{LEGACY_LOWER.title()}(?:[A-Z_]|$)|"
    rf"{LEGACY_LOWER.upper()}(?:_|$))"
)
_ENVIRONMENT = re.compile(rf"\b{LEGACY_LOWER.upper()}_[A-Z0-9_]+\b")
_ENTRY_POINT = re.compile(
    rf"(?:\b(?:from|import)\s+{LEGACY_LOWER}\b|"
    rf"(?:^|[\s\"'`])(?:-m\s+)?{LEGACY_LOWER}(?:\s+--|[:.]\w+|[\"']?\s*=)|"
    rf"(?:^|[;`]|&&|\|\|)\s*(?:\S*/)?{LEGACY_LOWER}(?:\s+(?:--|work\b|bootstrap\b|verify\b)|$)|"
    rf"[\"']command[\"']\s*:\s*[\"']{LEGACY_LOWER}[\"']|"
    rf"\b{LEGACY_LOWER.title()}(?:Error)?\b|"
    rf"(?:src/|skills/){LEGACY_LOWER}\b|agent-{LEGACY_LOWER}-happy-path)",
    re.MULTILINE,
)
_MODULE_COMMAND = re.compile(rf"(?:^|\s)-m\s+{LEGACY_LOWER}(?:[.\s]|$)")
_EXECUTORS = {
    "subprocess.run", "subprocess.Popen", "subprocess.call",
    "subprocess.check_call", "subprocess.check_output", "os.system", "os.popen",
}


def _literal_strings(node, bindings, seen=frozenset()):
    """Resolve only explicit literals/aliases; never execute inspected code."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [value for item in node.elts for value in _literal_strings(item, bindings, seen)]
    if isinstance(node, ast.Name) and node.id not in seen and node.id in bindings:
        return _literal_strings(bindings[node.id], bindings, seen | {node.id})
    return []


def _qualified_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _qualified_name(node.value) + "." + node.attr
    return ""


def _live_namespace_hits(relative: str, text: str) -> list[str]:
    """A bounded static rename check; not whole-program dynamic analysis."""
    if not relative.endswith(".py"):
        return [str(number) for number, line in enumerate(text.splitlines(), 1)
                if _ENTRY_POINT.search(line) or _ENVIRONMENT.search(line)
                or _MODULE_COMMAND.search(line)]
    tree = ast.parse(text, filename=relative)
    hits = set()
    aliases, bindings = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update((item.asname or item.name, item.name) for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            aliases.update((item.asname or item.name, f"{node.module}.{item.name}")
                           for item in node.names)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [item.name.split(".")[0] for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]] + [item.name for item in node.names]
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names = [node.name]
        elif isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        if any(_IDENTIFIER.match(name) for name in names):
            hits.add(str(node.lineno))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _ENVIRONMENT.search(node.value):
                hits.add(str(node.lineno))
        if not isinstance(node, ast.Call):
            continue
        name = _qualified_name(node.func)
        first, _, tail = name.partition(".")
        name = aliases.get(first, first) + ("." + tail if tail else "")
        argument = node.args[0] if node.args else next(
            (item.value for item in node.keywords if item.arg in {"args", "name"}), None
        )
        values = _literal_strings(argument, bindings)
        if name in {"importlib.import_module", "__import__"}:
            if any(value.split(".")[0] == LEGACY_LOWER for value in values):
                hits.add(str(node.lineno))
        elif name in _EXECUTORS:
            command = " ".join(values)
            if _MODULE_COMMAND.search(command) or re.match(
                rf"^(?:\S*/)?{LEGACY_LOWER}(?:\s|$)", command
            ):
                hits.add(str(node.lineno))
    return sorted(hits, key=int)


def _namespace_occurrences():
    for path, relative in _files():
        if path == ALLOWLIST:
            continue
        if LEGACY_PATTERN.search(relative):
            yield relative, "path"
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for location in _live_namespace_hits(relative, text):
            yield relative, location


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
    offenders = [f"{relative}:{location}" for relative, location in _namespace_occurrences()
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
        ROOT / ".agents" / "skills" / "poise" / "SKILL.md",
        ROOT / ".agents" / "skills" / "poise-development" / "SKILL.md",
        ROOT / "docs" / "operations" / "replacement-upgrade.md",
    )
    assert all(path.is_file() for path in required)
    assert not (ROOT / ".agents" / "skills" / LEGACY_LOWER).exists()
    assert not (ROOT / ".agents" / "skills" / (LEGACY_LOWER + "-development")).exists()

    readme = (ROOT / "README.md").read_text()
    agents = (ROOT / "AGENTS.md").read_text()
    upgrade = (ROOT / "docs" / "operations" / "replacement-upgrade.md").read_text()
    assert ".venv/bin/poise --help" in readme
    assert "POISE_CONFIG" in readme and "POISE_SESSION" in readme
    assert ".agents/skills/poise/SKILL.md" in agents
    assert "tools/upgrade_to_ai_poise.py" in upgrade
    assert f"pip uninstall -y {('agent-' + LEGACY_LOWER + '-happy-path')}" in upgrade
    assert "ai-poise" in upgrade and "poise --help" in upgrade

    from poise.infrastructure.documentation_checks import audit_documentation
    result = audit_documentation(ROOT)
    assert not result["errors"], result["errors"]


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
    assert "агент при обычном создании нового Task использует automatic" in workflow
    assert "никогда не выбирает номер сам" in workflow
    assert "TaskRepository.allocate" in boundaries
    assert "не вычисляют номер" in boundaries
