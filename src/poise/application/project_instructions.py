"""Prepare current instructions once through their existing domain owners."""
from ..modules.skills.instruction_ports import InstructionSource
from ..modules.skills.instructions import (
    InstructionPolicy, parse_prepare_request, prepare_paths, select_materials,
    validate_previous, public_result,
)


class ProjectInstructions:
    def __init__(self, source: InstructionSource):
        self.source = source

    def prepare(self, request):
        request = parse_prepare_request(request)
        context = self.source.project_context(request['project_config'], request['checkout'])
        document, digest = self.source.policy_document(request['policy'])
        catalog = self.source.catalogue_metadata()
        policy = InstructionPolicy.from_configuration(document, catalog)
        paths = prepare_paths(request['paths'])
        validate_previous(request['previous'], context)
        rules = self.source.existing_rule_paths(context['checkout'], paths)
        specs = select_materials(policy, catalog, paths, rules)
        materials = self.source.observe_materials(context, specs)
        return public_result(context, digest, paths, materials, request['previous'])
