"""One generic catalogue and instruction preparation owner."""
from domain.generic import Problem, exact_fields, invalid, text
from domain.skills import (InstructionPolicy, SkillCatalog, prepare_paths, public_result,
                           select_materials, validate_previous)


class GenericSkills:
    def __init__(self, files):
        self.files = files

    def catalog(self, request):
        self.files.absolute(request['catalog'], '/input/catalog')
        self.files.absolute(request['instruction_root'], '/input/instruction_root')
        try:
            return SkillCatalog.from_configuration(self.files.read_json(request['catalog']))
        except (ValueError, OSError) as exc:
            raise Problem('invalid_catalog', {'reason': str(exc)}, 'correct:catalog') from exc

    def list(self, request):
        exact_fields(request, {'catalog', 'instruction_root'}, '/input')
        return {'skills': self.catalog(request).as_dict()['skills']}

    def read(self, request):
        exact_fields(request, {'catalog', 'instruction_root', 'skill_ids'}, '/input')
        try:
            return {'skills': self.catalog(request).select(request['skill_ids'])}
        except ValueError as exc:
            raise Problem('invalid_selection', {'reason': str(exc)}, 'correct:selection') from exc


class InstructionPreparation:
    def __init__(self, files, skills):
        self.files, self.skills = files, skills

    def prepare(self, request):
        fields = {'catalog', 'instruction_root', 'project', 'checkout', 'policy', 'paths', 'previous'}
        exact_fields(request, fields, '/input')
        text(request['project'], '/input/project')
        if not isinstance(request['paths'], list):
            invalid('/input/paths')
        checkout = self.files.checkout(request['checkout'])
        self.files.absolute(request['policy'], '/input/policy')
        catalog = self.skills.catalog(request)
        context = {'project': request['project'], 'checkout': checkout,
                   'instruction_root': request['instruction_root']}
        try:
            policy = InstructionPolicy.from_configuration(self.files.read_json(request['policy']), catalog)
            paths = prepare_paths(request['paths'])
            validate_previous(request['previous'], context)
            rules = self.files.rule_paths(checkout, paths)
            specs = select_materials(policy, catalog, paths, rules)
            result = public_result(context, paths, self.files.materials(context, specs), request['previous'])
        except (ValueError, TypeError, OSError) as exc:
            raise Problem('invalid_preparation', {'reason': str(exc)}, 'correct:preparation') from exc
        if result['status'] == 'needs_inputs':
            raise Problem('materials_unavailable', {'diagnostics': result['diagnostics']},
                          'restore:materials', 'needs_inputs', result)
        return result
