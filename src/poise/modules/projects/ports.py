from typing import Protocol
from .domain import ProjectBlueprint


class ProjectSetupPort(Protocol):
    def template(self,selection:dict)->ProjectBlueprint: ...
    def apply(self,request:dict)->dict: ...
