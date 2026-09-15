"""One route composition for direct use, bootstrap and explicit refresh."""
from ..modules.skills.routing import parse_route, select_route


class DevelopmentRouting:
    def __init__(self, catalog, selection, policy, files):
        self.catalog, self.selection, self.policy, self.files = catalog, selection, policy, files

    def route(self, request, snapshot=None):
        req = parse_route(request)
        checkout = self.files.checkout(req['checkout'])
        req['checkout'] = checkout
        route = select_route(req, self.selection, self.policy)
        impact = self.files.test_impact(checkout, req['changed_paths'])
        route['checks']['suggested_packages'] = sorted(set(
            route['checks']['suggested_packages'] + impact['suggested_packages']))
        route['missing_inputs'].extend('unmapped_test_input:' + path for path in impact['unmapped_paths'])
        rules, missing_rules = self.files.rules(checkout, req['scope_paths'] + req['changed_paths'])
        skill_files, missing_skills = self.files.skills(checkout, self.catalog.select(route['skills']))
        missing = sorted(set(route['missing_inputs'] + missing_rules + missing_skills))
        result = self.files.seal({
            'schema': 'ai-poise-development-route-1',
            'status': 'needs_inputs' if missing else 'ready',
            'input': req, **route, 'test_impact': impact, 'rules': rules, 'skill_files': skill_files,
            'missing_inputs': missing, 'result_contract': req['result_contract'],
            'configuration_digests': self.files.configuration_digests,
        })
        if snapshot is not None:
            self.files.write_snapshot(snapshot, result)
        return result

    def check_authoring_boundaries(self, checkout, handler, paths, deleted_paths):
        gate = self.policy.document.get('authoring_boundary_gate')
        if gate is None:
            return {'status': 'not_configured', 'passed': None}
        if handler not in gate['handlers']:
            return {'status': 'not_applicable', 'passed': None}
        return self.files.check_boundaries(checkout, paths, deleted_paths)

    def read_snapshot(self, path):
        # Replay is a sealed historical selection, not proof of current bytes.
        return self.files.read_snapshot(path)
