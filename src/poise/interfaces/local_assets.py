"""Strict request transport; no live project config or session establishment."""
from __future__ import annotations
import json
from ..common import PoiseError
from ..composition import local_assets_tools
from ..infrastructure.goal_config import strict_json
from ..modules.local_assets.domain import AssetDeclaration, LocalAssetError, RestoreRequest


def execute(manifest_path, stdin, stdout):
    try:
        try:
            raw_request = strict_json(stdin.read())
        except (PoiseError, OSError):
            raise LocalAssetError("invalid_request") from None
        request = RestoreRequest.parse(raw_request)
        try:
            raw_manifest = strict_json(manifest_path.read_bytes())
        except (PoiseError, OSError):
            raise LocalAssetError("invalid_manifest") from None
        declaration = AssetDeclaration.parse(raw_manifest)
        reply = local_assets_tools().restore(declaration, request)
        exit_code = 2 if reply["status"] == "incomplete" else 0
    except LocalAssetError as exc:
        reply, exit_code = exc.reply(), exc.exit_code
    print(json.dumps(reply, ensure_ascii=False), file=stdout)
    return exit_code
