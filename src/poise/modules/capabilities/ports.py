from typing import Protocol
from .domain import ProbeSpec, CapabilitySummary


class ProbeExecutor(Protocol):
    def observe(self, spec: ProbeSpec, workspace: str) -> dict: ...


class SummaryStore(Protocol):
    def load(self) -> CapabilitySummary: ...
    def save(self, summary: CapabilitySummary) -> None: ...
