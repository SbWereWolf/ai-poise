"""Coordinate configured project execution through the existing owners."""
from ..modules.projects.launch import ProjectLaunchSettings
from ..modules.projects.ports import ProjectLaunchPort


class ProjectLaunchCommands:
    def __init__(self, port: ProjectLaunchPort):
        self.port = port

    def execute(self, settings: ProjectLaunchSettings, arguments: list[str]) -> None:
        listing = self.port.list_projects(settings)
        config_path = settings.select_project(listing)
        self.port.require_data_placement(settings, config_path)
        self.port.execute(settings.plan_command(arguments), config_path)
