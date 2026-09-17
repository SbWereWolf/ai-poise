from typing import Protocol
from .domain import ProjectBlueprint


class ProjectSetupPort(Protocol):
    def template(self,selection:dict)->ProjectBlueprint: ...
    def apply(self,request:dict)->dict: ...
    def list(self)->dict: ...


class ProjectAvailabilityPort(Protocol):
    """Read a configured project's Task/Sprint snapshot without runtime setup."""
    def startable(self, project: dict) -> list[dict]: ...


class ProjectConfigUpdatePort(Protocol):
    def apply(self,request:dict)->dict: ...
