"""Validate the complete explicit batch before publishing missing assets."""
from __future__ import annotations
import hashlib
from ..modules.local_assets.domain import AssetDeclaration, LocalAssetError, PublicationFailure, RestoreRequest
from ..modules.local_assets.ports import LocalAssetsPort


class LocalAssetsRestore:
    def __init__(self, files: LocalAssetsPort):
        self.files = files

    def restore(self, declaration: AssetDeclaration, request: RestoreRequest) -> dict:
        declaration.validate_identity(request)
        outcomes: list[dict] = []
        publishing = False
        try:
            with self.files.open_roots(request) as storage:
                plan = []
                for asset in declaration.assets:
                    content = storage.source_bytes(asset)
                    if hashlib.sha256(content).hexdigest() != asset.sha256:
                        raise LocalAssetError("source_digest_mismatch", asset.path)
                    storage.destination_exists(asset)
                    plan.append((asset, content))
                publishing = True
                for asset, content in plan:
                    try:
                        status = storage.publish_missing(asset, content)
                    except PublicationFailure as exc:
                        if exc.outcome is not None:
                            outcomes.append({"path": asset.path, "status": exc.outcome})
                        raise
                    outcomes.append({"path": asset.path, "status": status})
        except (LocalAssetError, OSError) as exc:
            error = exc if isinstance(exc, LocalAssetError) else LocalAssetError("io_failure")
            if not publishing:
                raise error from exc
            # Per-file publication is atomic; a multi-file I/O failure is not a rollback.
            reply = error.reply()
            reply.update(status="incomplete", project=request.project,
                         target_root=request.target_root, assets=outcomes)
            return reply
        return {"status": "restored" if any(item["status"] == "created" for item in outcomes) else "unchanged",
                "project": request.project, "target_root": request.target_root, "assets": outcomes}
