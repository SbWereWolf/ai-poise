from __future__ import annotations

from ..modules.result_integration.domain import IntegrationIntent


class ResultIntegrationCommands:
    """Application boundary for one accepted-result integration request."""

    def __init__(self, port):
        self.port = port

    def apply(self, packet):
        return self.port.apply(IntegrationIntent.parse(packet))

    def prepare_source(self, packet):
        return self.port.prepare_source(IntegrationIntent.parse(packet))

    def query(self, task_id, request_id):
        return self.port.query(task_id, request_id)
