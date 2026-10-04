"""Filesystem observations and create-only publication owned by the adapter."""
from __future__ import annotations
from contextlib import AbstractContextManager
from typing import Protocol
from .domain import AssetSpec, RestoreRequest


class LocalAssetsStorage(Protocol):
    def source_bytes(self, asset: AssetSpec) -> bytes: ...
    def destination_exists(self, asset: AssetSpec) -> bool: ...
    def publish_missing(self, asset: AssetSpec, content: bytes) -> str: ...


class LocalAssetsPort(Protocol):
    def open_roots(self, request: RestoreRequest) -> AbstractContextManager[LocalAssetsStorage]: ...
