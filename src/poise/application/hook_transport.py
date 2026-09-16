"""One desired hook definition per batch, never one write per event."""
from ..modules.hook_transport.domain import HookDefinition
from ..modules.hook_transport.ports import HookRepository
from ..modules.artifact_factory.domain import exact
from ..modules.capabilities.domain import nonempty
from ..modules.foundation.errors import PoiseError


class HookCommands:
    def __init__(self, repository: HookRepository):self.repository=repository

    def install(self,packet):
        exact(packet,{'request_id','expected_revision','definition'},'hook install packet')
        nonempty(packet['request_id'],'request_id')
        if packet['expected_revision'] is not None:nonempty(packet['expected_revision'],'expected_revision')
        definition=HookDefinition.parse(packet['definition'])
        return self.repository.install(packet['request_id'],packet['expected_revision'],definition)

    def reconcile(self,packet):
        exact(packet,{'request_id','expected_revision','previous_installation_id',
                      'live_definition_path','definition'},'hook reconciliation packet')
        for key in ('request_id','expected_revision','previous_installation_id','live_definition_path'):
            nonempty(packet[key],key)
        definition=HookDefinition.parse(packet['definition'])
        return self.repository.reconcile(packet['request_id'],packet['expected_revision'],definition,
                                         packet['previous_installation_id'],packet['live_definition_path'])
