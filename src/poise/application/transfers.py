"""One batch entry; the adapter coordinates existing owner repositories and I/O."""
from ..modules.transfers.domain import TransferPolicy, TransferRequest
from ..modules.transfers.ports import TransferPort


class TransferCommands:
    def __init__(self, port: TransferPort, policy: dict):
        self.port = port
        self.policy = TransferPolicy.parse(policy)

    def apply(self, packet: dict) -> dict:
        request = TransferRequest.parse(packet,self.policy.data).data
        return self.port.export(request) if request['action']=='export' else self.port.restore(request)
