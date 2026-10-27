"""Preparation and observation of the application's own state."""
from domain.generic import Problem


class StateOperations:
    def __init__(self, store, target_version):
        self.store = store
        self.target_version = target_version

    def result(self, version, applied):
        if version != self.target_version:
            code = 'state_not_prepared' if version == 0 else 'state_incompatible'
            action = 'prepare:state' if version == 0 else 'select:state'
            raise Problem(code, {'version': version}, action, 'unavailable')
        return {'database': str(self.store.database), 'version': version,
                'applied_versions': applied}

    def prepare(self):
        version, applied = self.store.prepare()
        return self.result(version, applied)

    def status(self):
        return self.result(self.store.version(), [])
