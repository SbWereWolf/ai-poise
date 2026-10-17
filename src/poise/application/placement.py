"""One placement admission/effect coordinator; no Task lifecycle or filesystem code."""
import hashlib
from ..modules.foundation.errors import LockUnavailable, PoiseError
from ..modules.placement.contracts import RESULT_SCHEMA, diagnostic
from ..modules.placement.policy import PlacementPolicy
from ..modules.placement.state import PlacementState
from ..modules.placement.ports import PlacementWorkspace, CurrentPlacementOwners


def checksum(raw):
    return None if raw is None else hashlib.sha256(raw).hexdigest()


class PlacementCommands:
    def __init__(self, project, checkout, caller_key, policy: PlacementPolicy, policy_sha256,
                 workspace: PlacementWorkspace, owners: CurrentPlacementOwners, decode_state):
        self.project, self.checkout, self.caller_key = project, checkout, caller_key
        self.policy, self.policy_sha256 = policy, policy_sha256
        self.workspace, self.owners, self.decode_state = workspace, owners, decode_state

    def execute(self, request):
        owners = self.owners.current(self.policy.needed_owners(request.items)) if self.owners is not None else {}
        rows, diagnostics = self.policy.candidates(request.items, self.project, owners)
        state_view = {'path': self.workspace.path, 'before_sha256': None, 'after_sha256': None,
                      'observed_sha256': None, 'effect': 'none', 'confirmation': 'not_applicable'}

        def failure(code, action):
            diagnostics.append(diagnostic(code, None, 'state', action))
            return self._result(rows, diagnostics, state_view)

        try:
            with self.workspace.locked(request.operation):
                try:
                    before = self.workspace.read()
                except OSError:
                    return failure('state_read', 'inspect:state')
                state_view['before_sha256'] = state_view['observed_sha256'] = checksum(before)
                if before is None:
                    if request.operation == 'check':
                        return failure('state_missing', 'prepare')
                    try:
                        state = PlacementState.initial(self.project, self.checkout, self.caller_key,
                                                       self.workspace.baseline())
                    except OSError:
                        return failure('state_read', 'inspect:state')
                else:
                    try:
                        state = PlacementState.parse(self.decode_state(before), self.project,
                                                     self.checkout, self.caller_key)
                    except (PoiseError, ValueError, UnicodeError, RecursionError):
                        return failure('invalid_state', 'inspect:state')
                next_state, rows, diagnostics, allowed = state.admit(
                    request, self.policy, self.checkout, rows, diagnostics)
                if request.operation == 'check':
                    return self._result(rows, diagnostics, state_view)
                # A denied batch never rewrites existing declarations or stores supplied grants.
                after = self.workspace.encode(next_state.document) if allowed or before is None else before
                state_view['after_sha256'] = checksum(after)
                try:
                    if after == before:
                        self.workspace.confirm()
                        state_view.update(effect='unchanged', confirmation='confirmed')
                    else:
                        self.workspace.write(after)
                        state_view.update(effect='committed', confirmation='confirmed')
                    observed = self.workspace.read()
                    state_view['observed_sha256'] = checksum(observed)
                    if observed != after:
                        raise OSError('Placement state readback differs from the intended image')
                    if allowed:
                        for row in rows:
                            row['reserved'] = True
                except OSError:
                    state_view.update(effect='unconfirmed', confirmation='unconfirmed')
                    for row in rows:
                        row['reserved'] = False
                    try:
                        state_view['observed_sha256'] = checksum(self.workspace.read())
                    except OSError:
                        state_view['observed_sha256'] = None
                    return failure('state_write', 'retry:prepare')
                return self._result(rows, diagnostics, state_view)
        except (LockUnavailable, OSError):
            return failure('state_lock', 'retry:' + request.operation)

    def _result(self, rows, diagnostics, state_view):
        if diagnostics and any(v['code'] != 'approval_required' for v in diagnostics):
            status = 'needs_inputs'
        elif any(v['status'] == 'needs_agreement' for v in rows):
            status = 'needs_agreement'
        else:
            status = 'ready'
        return {'schema': RESULT_SCHEMA, 'status': status, 'project': self.project,
                'checkout': self.checkout, 'policy_sha256': self.policy_sha256,
                'state': state_view, 'items': rows,
                'diagnostics': sorted(diagnostics, key=lambda v: (
                    v['key'] is not None, v['key'] or '', v['code'], v['field'], v['action']))}
