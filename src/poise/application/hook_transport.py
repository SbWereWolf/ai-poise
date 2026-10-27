"""One desired hook definition per batch, never one write per event."""
from ..modules.hook_transport.domain import HookDefinition
from ..modules.hook_transport.ports import HookRepository
from ..modules.foundation.validation import nonempty, validate_exact_keys
from ..modules.foundation.errors import PoiseError


class HookCommands:
    def __init__(self, repository: HookRepository):self.repository=repository

    def relocate(self, target, installation, request_id):
        validate_exact_keys(target, {'installation_id', 'settings_path', 'settings'}, 'hook relocation', PoiseError)
        return self.repository.relocate(target, installation, request_id)

    def install(self,packet):
        validate_exact_keys(packet,{'request_id','expected_revision','definition'},'hook install packet',PoiseError)
        nonempty(packet['request_id'],'request_id')
        if packet['expected_revision'] is not None:nonempty(packet['expected_revision'],'expected_revision')
        definition=HookDefinition.parse(packet['definition'])
        return self.repository.install(packet['request_id'],packet['expected_revision'],definition)

    def reconcile(self,packet):
        validate_exact_keys(packet,{'request_id','expected_revision','previous_installation_id',
                      'live_definition_path','definition'},'hook reconciliation packet',PoiseError)
        for key in ('request_id','expected_revision','previous_installation_id','live_definition_path'):
            nonempty(packet[key],key)
        definition=HookDefinition.parse(packet['definition'])
        return self.repository.reconcile(packet['request_id'],packet['expected_revision'],definition,
                                         packet['previous_installation_id'],packet['live_definition_path'])
