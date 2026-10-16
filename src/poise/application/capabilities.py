"""Validate the whole plan, then obtain independent capability observations."""
from ..modules.capabilities.domain import ProbeSpec, CapabilitySummary, positive
from ..modules.capabilities.ports import ProbeExecutor, SummaryStore
from ..modules.accounting.clock import Clock
from ..modules.foundation.errors import PoiseError


class CapabilityChecks:
    def __init__(self, executor: ProbeExecutor, max_probes: int):
        positive(max_probes,'max_probes',True)
        self.executor,self.max_probes=executor,max_probes

    def run(self, values, workspace):
        if not isinstance(values,list) or len(values)>self.max_probes:
            raise PoiseError('Probe batch exceeds its explicit limit')
        specs=[ProbeSpec.parse(v) for v in values]
        names=[s.data['id'] for s in specs]
        if len(set(names))!=len(names):raise PoiseError('Duplicate capability identity')
        observed=[self.executor.observe(s,workspace) for s in specs]
        missing=[s.data['id'] for s,o in zip(specs,observed) if s.data['required'] and o['status']!='available']
        return {'ready':not missing,'missing_required':missing,'observations':observed}


class SessionReadiness:
    """Read completed data or explicitly observe once through the existing checks."""

    def __init__(self, checks: CapabilityChecks, store: SummaryStore):
        self.checks, self.store = checks, store

    def read(self, session_id):
        summary = self.store.load()
        if summary.data['session_id'] != session_id:
            raise PoiseError('Saved capability summary does not belong to this session')
        return summary

    def refresh(self, session_id, workspace, probe_values, clock: Clock):
        result = self.checks.run(probe_values, workspace)
        observed = [{**item, 'required': probe['required'], 'reason': item.get('reason')}
                    for probe, item in zip(probe_values, result['observations'])]
        summary = CapabilitySummary.from_mapping({
            'schema': 'capability-summary-1', 'session_id': session_id, 'workspace': workspace,
            'checked_at': clock.observe().audit_utc, 'ready': result['ready'],
            'missing_required': result['missing_required'], 'observations': observed,
        })
        self.store.save(summary)
        return summary
