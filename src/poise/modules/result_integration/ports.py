from typing import Protocol

from .domain import IntegrationScopeRelocation


class IntegrationScopeRepository(Protocol):
    def records(self, task_ids: list[str]) -> list[tuple[int, IntegrationScopeRelocation]]: ...
