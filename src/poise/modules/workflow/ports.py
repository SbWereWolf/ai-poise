from typing import Protocol


class RouteCountLimitMigration(Protocol):
    def migrate(self) -> dict: ...
