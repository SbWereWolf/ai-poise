"""Validate the whole plan, then obtain independent capability observations."""
from ..modules.capabilities.domain import ProbeSpec, positive
from ..modules.capabilities.ports import ProbeExecutor
from ..modules.foundation.errors import HarnessError


class CapabilityChecks:
    def __init__(self, executor: ProbeExecutor, max_probes: int):
        positive(max_probes,'max_probes',True)
        self.executor,self.max_probes=executor,max_probes

    def run(self, values, workspace):
        if not isinstance(values,list) or len(values)>self.max_probes:
            raise HarnessError('Probe batch exceeds its explicit limit')
        specs=[ProbeSpec.parse(v) for v in values]
        names=[s.data['id'] for s in specs]
        if len(set(names))!=len(names):raise HarnessError('Duplicate capability identity')
        observed=[self.executor.observe(s,workspace) for s in specs]
        missing=[s.data['id'] for s,o in zip(specs,observed) if s.data['required'] and o['status']!='available']
        return {'ready':not missing,'missing_required':missing,'observations':observed}
