"""Prepare current instructions once through their existing domain owners."""
from ..modules.skills.instruction_ports import InstructionSource
from ..modules.skills.instructions import (
    InstructionPolicy, parse_prepare_request, prepare_paths, select_materials,
    validate_previous, public_result,
)
from ..modules.skills.instruction_delivery import (
    delivery_result, parse_bounded_delivery_request, parse_delivery_request, select_delivery_materials,
)
from ..modules.skills.portable_instructions import REQUEST_SCHEMA, execute


class ProjectInstructions:
    def __init__(self, source: InstructionSource):
        self.source = source

    def prepare(self, request):
        return self._observe(parse_prepare_request(request), None, None)

    def deliver(self, request):
        bounded = isinstance(request, dict) and ('budget' in request or 'continuation' in request)
        request = (parse_bounded_delivery_request(request) if bounded else
                   parse_delivery_request(request))
        preparation = {'operation': 'prepare', 'previous': None,
                       **{key: request[key] for key in ('project_config', 'checkout', 'policy', 'paths')}}
        observation = self._observe(preparation, request['stage'], request['tool'])
        if bounded:
            return execute({
                'schema': REQUEST_SCHEMA, 'operation': 'deliver_selected', 'selection': observation,
                **{key: request[key] for key in (
                    'context', 'stage', 'previous', 'retained', 'budget', 'continuation')},
            })
        return delivery_result(observation, request)

    def _observe(self, request, stage, tool):
        context = self.source.project_context(request['project_config'], request['checkout'])
        document, digest = self.source.policy_document(request['policy'])
        catalog = self.source.catalogue_metadata()
        policy = InstructionPolicy.from_configuration(document, catalog)
        paths = prepare_paths(request['paths'])
        validate_previous(request['previous'], context)
        rules = self.source.existing_rule_paths(context['checkout'], paths)
        specs = select_materials(policy, catalog, paths, rules)
        if stage is not None:
            specs = select_delivery_materials(specs, catalog, stage, paths, tool)
        materials = self.source.observe_materials(context, specs)
        return public_result(context, digest, paths, materials, request['previous'])
