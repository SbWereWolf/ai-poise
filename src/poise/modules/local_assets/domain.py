"""Explicit local-asset restore contract; no filesystem or repair execution."""
from __future__ import annotations

from dataclasses import dataclass
import re


ERRORS = {
    "missing_working_source": (3, "Declared working source asset is missing.", "Supply the matching snapshot asset; owner repair is unavailable."),
    "missing_repairable_source": (3, "Declared working source asset is missing.", "Supply the matching snapshot asset or run the declared owner repair, then retry."),
    "missing_example_source": (3, "Declared example source asset is missing.", "Supply the declared example in the source snapshot and retry."),
    "missing_tracked_source": (3, "Declared tracked source asset is missing.", "Supply the declared tracked file in the source snapshot and retry."),
    "project_mismatch": (2, "Project identity does not match the asset declaration.", "Select the declared project ID and retry."),
    "target_root_mismatch": (2, "Target root does not match the asset declaration.", "Select the declared target Git checkout root and retry."),
    "unsafe_asset_path": (2, "Asset path is not a safe relative path.", "Correct the asset declaration path and retry."),
    "unsafe_source_path": (2, "Source path is not a safe relative path.", "Correct the source declaration path and retry."),
    "unsafe_source_root": (2, "Source root is not a safe existing directory.", "Select a real source snapshot directory and retry."),
    "unsafe_symlink": (2, "Asset path contains a symlink.", "Replace the symlinked path with a real path and retry."),
    "source_digest_mismatch": (2, "Source asset digest does not match the declaration.", "Restore the declared source bytes or correct the declaration through its owner."),
    "invalid_request": (2, "Restore request is invalid.", "Supply a valid local-assets-restore-1 request and retry."),
    "invalid_manifest": (2, "Asset declaration is invalid.", "Supply a valid local-assets-1 declaration and retry."),
    "invalid_target_root": (2, "Target root is not an existing Git checkout root.", "Select the declared existing Git checkout root and retry."),
    "invalid_source_asset": (2, "Source asset is not a regular file.", "Supply a regular source snapshot file and retry."),
    "invalid_destination_asset": (2, "Destination asset is not a regular file.", "Resolve the declared destination through its owner and retry."),
    "asset_path_collision": (2, "Declared destination asset paths collide.", "Declare independent file destinations and retry."),
    "io_failure": (2, "Local asset I/O failed.", "Inspect filesystem access and the reported outcomes, then retry with the same declaration."),
}


class LocalAssetError(Exception):
    def __init__(self, code: str, asset: str | None = None):
        self.code = code
        self.asset = asset
        super().__init__(code)

    @property
    def exit_code(self) -> int:
        return ERRORS[self.code][0]

    def reply(self) -> dict:
        _, reason, recovery = ERRORS[self.code]
        return {"status": "rejected", "code": self.code, "asset": self.asset,
                "reason": reason, "recovery": recovery}


class PublicationFailure(LocalAssetError):
    """A terminal I/O failure with the current file's known publication outcome."""
    def __init__(self, asset: str, outcome: str | None):
        super().__init__("io_failure", asset)
        self.outcome = outcome


def shape(value: object, keys: set[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise LocalAssetError(code)
    return value


def text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise LocalAssetError(code)
    return value


def relative(value: str, code: str, asset: str) -> str:
    if (value.startswith("/") or "\\" in value or ":" in value
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise LocalAssetError(code, asset)
    return value


@dataclass(frozen=True)
class RestoreRequest:
    project: str
    target_root: str
    source_root: str

    @classmethod
    def parse(cls, value: object) -> RestoreRequest:
        data = shape(value, {"schema", "project", "target_root", "source_root"}, "invalid_request")
        if data["schema"] != "local-assets-restore-1":
            raise LocalAssetError("invalid_request")
        return cls(*(text(data[key], "invalid_request") for key in ("project", "target_root", "source_root")))


@dataclass(frozen=True)
class AssetSpec:
    path: str
    source_path: str
    sha256: str
    kind: str
    mode: int
    repair: str | None

    @classmethod
    def parse(cls, value: object) -> AssetSpec:
        data = shape(value, {"path", "source_path", "sha256", "kind", "mode", "repair"}, "invalid_manifest")
        path = text(data["path"], "invalid_manifest")
        source = text(data["source_path"], "invalid_manifest")
        digest = text(data["sha256"], "invalid_manifest")
        kind = text(data["kind"], "invalid_manifest")
        mode, repair = data["mode"], data["repair"]
        if (not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
                or kind not in ("tracked", "versioned_example", "untracked_example", "working")
                or type(mode) is not int or not 0 <= mode <= 0o777
                or (repair is not None and (kind != "working" or not isinstance(repair, str) or not repair.strip()))):
            raise LocalAssetError("invalid_manifest")
        return cls(relative(path, "unsafe_asset_path", path),
                   relative(source, "unsafe_source_path", path), digest.lower(), kind, mode, repair)

    def missing(self) -> LocalAssetError:
        if self.kind == "tracked":
            code = "missing_tracked_source"
        elif self.kind in ("versioned_example", "untracked_example"):
            code = "missing_example_source"
        else:
            code = "missing_repairable_source" if self.repair is not None else "missing_working_source"
        return LocalAssetError(code, self.path)


@dataclass(frozen=True)
class AssetDeclaration:
    project: str
    target_root: str
    assets: tuple[AssetSpec, ...]

    @classmethod
    def parse(cls, value: object) -> AssetDeclaration:
        data = shape(value, {"schema", "project", "target_root", "assets"}, "invalid_manifest")
        if data["schema"] != "local-assets-1" or not isinstance(data["assets"], list):
            raise LocalAssetError("invalid_manifest")
        project = text(data["project"], "invalid_manifest")
        root = text(data["target_root"], "invalid_manifest")
        assets = tuple(AssetSpec.parse(item) for item in data["assets"])
        paths = {asset.path for asset in assets}
        if len(paths) != len(assets):
            raise LocalAssetError("invalid_manifest")
        for path in paths:
            parts = path.split("/")
            if any("/".join(parts[:i]) in paths for i in range(1, len(parts))):
                raise LocalAssetError("asset_path_collision")
        return cls(project, root, assets)

    def validate_identity(self, request: RestoreRequest) -> None:
        if request.project != self.project:
            raise LocalAssetError("project_mismatch")
        if request.target_root != self.target_root:
            raise LocalAssetError("target_root_mismatch")
