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
        rules, missing_rules = self.files.rules(checkout, req['scope_paths'] + req['changed_paths'])
        skill_files, missing_skills = self.files.skills(checkout, self.catalog.select(route['skills']))
        missing = sorted(set(route['missing_inputs'] + missing_rules + missing_skills))
        result = self.files.seal({
            'schema': 'ai-poise-development-route-1',
            'status': 'needs_inputs' if missing else 'ready',
            'input': req, **route, 'rules': rules, 'skill_files': skill_files,
            'missing_inputs': missing, 'result_contract': req['result_contract'],
            'configuration_digests': self.files.configuration_digests,
        })
        if snapshot is not None:
            self.files.write_snapshot(snapshot, result)
        return result

    def read_snapshot(self, path):
        # Replay is a sealed historical selection, not proof of current bytes.
        return self.files.read_snapshot(path)
