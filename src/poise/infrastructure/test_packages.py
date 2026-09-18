from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import tomllib

from ..common import PoiseError


_MEMBER_KINDS = ("source", "tests", "fixtures")


def _relative_pattern(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise PoiseError(f"{where}: expected repository-relative POSIX pattern")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise PoiseError(f"{where}: pattern must stay inside the AI-poise checkout")
    return value


@dataclass(frozen=True)
class TestPackage:
    id: str
    owner: str
    integration_boundaries: tuple[str, ...]
    patterns: dict[str, tuple[str, ...]]


class AiPoiseTestPackages:
    """AI-poise's own test-package declarations.

    The catalogue location and the checkout being inspected are independent
    inputs.  The checkout is data: it never changes how Poise resolves its own
    executable code or configuration.
    """

    def __init__(self, packages: tuple[TestPackage, ...]):
        self._packages = packages
        self._by_id = {package.id: package for package in packages}

    @classmethod
    def load(cls, catalog_path: Path) -> "AiPoiseTestPackages":
        try:
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PoiseError(f"Cannot read AI-poise test-package catalogue: {exc}") from exc
        if not isinstance(raw, dict) or set(raw) != {"schema", "packages"}:
            raise PoiseError("AI-poise test-package catalogue has an invalid top-level shape")
        if raw["schema"] != "ai-poise-test-packages-2" or not isinstance(raw["packages"], list):
            raise PoiseError("Unsupported AI-poise test-package schema")
        packages = []
        ids = set()
        for index, item in enumerate(raw["packages"]):
            where = f"packages[{index}]"
            if not isinstance(item, dict) or set(item) != {
                "id", "owner", "integration_boundaries", "members"
            }:
                raise PoiseError(f"{where}: invalid package declaration")
            package_id = item["id"]
            owner = item["owner"]
            boundaries = item["integration_boundaries"]
            members = item["members"]
            if not isinstance(package_id, str) or not package_id or package_id in ids:
                raise PoiseError(f"{where}.id: expected unique non-empty id")
            if not isinstance(owner, str) or not owner:
                raise PoiseError(f"{where}.owner: expected non-empty owner")
            if (not isinstance(boundaries, list)
                    or any(not isinstance(value, str) or not value for value in boundaries)
                    or len(boundaries) != len(set(boundaries))):
                raise PoiseError(f"{where}.integration_boundaries: expected unique ids")
            if not isinstance(members, dict) or set(members) != set(_MEMBER_KINDS):
                raise PoiseError(f"{where}.members: expected {_MEMBER_KINDS}")
            patterns = {}
            for kind in _MEMBER_KINDS:
                values = members[kind]
                if (not isinstance(values, list)
                        or any(not isinstance(value, str) for value in values)
                        or len(values) != len(set(values))):
                    raise PoiseError(f"{where}.members.{kind}: expected unique patterns")
                patterns[kind] = tuple(
                    _relative_pattern(value, f"{where}.members.{kind}") for value in values
                )
            ids.add(package_id)
            packages.append(TestPackage(package_id, owner, tuple(boundaries), patterns))
        unknown = {
            boundary
            for package in packages
            for boundary in package.integration_boundaries
            if boundary not in ids
        }
        if unknown:
            raise PoiseError(f"Unknown AI-poise package boundaries: {sorted(unknown)}")
        return cls(tuple(packages))

    @property
    def package_ids(self) -> tuple[str, ...]:
        return tuple(package.id for package in self._packages)

    def package(self, package_id: str) -> TestPackage:
        try:
            return self._by_id[package_id]
        except KeyError as exc:
            raise PoiseError(f"Unknown AI-poise test package: {package_id}") from exc

    @staticmethod
    def _assert_ai_poise_checkout(checkout: Path) -> Path:
        checkout = checkout.resolve()
        pyproject = checkout / "pyproject.toml"
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
            raise PoiseError("Test-package checkout must be an AI-poise checkout") from exc
        if project.get("name") != "ai-poise":
            raise PoiseError("Test-package checkout must have project.name = ai-poise")
        return checkout

    def membership(self, checkout: Path, package_id: str) -> dict:
        root = self._assert_ai_poise_checkout(checkout)
        package = self.package(package_id)
        members: dict[str, list[str]] = {}
        for kind in _MEMBER_KINDS:
            found = set()
            for pattern in package.patterns[kind]:
                matches = [path for path in root.glob(pattern) if path.is_file()]
                if not matches:
                    raise PoiseError(
                        f"AI-poise test package {package_id}: pattern matched no files: {pattern}"
                    )
                for path in matches:
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root):
                        raise PoiseError(
                            f"AI-poise test package {package_id}: member escapes checkout: {path}"
                        )
                    found.add(resolved.relative_to(root).as_posix())
            members[kind] = sorted(found)
        flattened = [path for kind in _MEMBER_KINDS for path in members[kind]]
        if len(flattened) != len(set(flattened)):
            raise PoiseError(f"AI-poise test package {package_id}: member categories overlap")
        return {
            "id": package.id,
            "owner": package.owner,
            "integration_boundaries": list(package.integration_boundaries),
            "members": members,
        }

    def impact(self, checkout: Path, changed_paths: list[str]) -> dict:
        """Inspect declared input paths, including deleted files, without execution."""
        from ..modules.verification.impact import select_package_impact
        root = self._assert_ai_poise_checkout(checkout)
        if not isinstance(changed_paths, list) or len(changed_paths) > 4096:
            raise PoiseError("Expected a bounded list of changed AI-poise paths")
        for value in changed_paths:
            name = _relative_pattern(value, "changed_paths")
            path = PurePosixPath(name)
            if (path.as_posix() != name or name == '.' or len(path.parts) > 128
                    or any(c in name for c in '*?[]') or not (root / name).resolve().is_relative_to(root)):
                raise PoiseError("Changed paths must be concrete relative files inside the AI-poise checkout")
        declarations = [{'id': p.id, 'owner': p.owner,
                         'integration_boundaries': list(p.integration_boundaries),
                         'members': p.patterns} for p in self._packages]
        return select_package_impact(declarations, changed_paths)

    def enumerate(self, checkout: Path) -> list[dict]:
        return [self.membership(checkout, package_id) for package_id in self.package_ids]
