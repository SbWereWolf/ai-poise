from typing import Protocol
from .domain import HookDefinition


class HookRepository(Protocol):
    def install(self,request_id: str,expected_revision: str | None,definition: HookDefinition) -> dict: ...
