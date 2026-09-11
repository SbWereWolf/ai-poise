from typing import Protocol
from .domain import ProbeSpec


class ProbeExecutor(Protocol):
    def observe(self, spec: ProbeSpec, workspace: str) -> dict: ...
