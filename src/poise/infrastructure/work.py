from pathlib import Path
from ..common import descendant
from .artifact_factory import FileArtifactFactory
from .sqlite.work_packets import SqliteWorkPacketRepository


class WorkResources:
    """Composition-side file/receipt adapter, not a generic editor of task tables."""
    def __init__(self,runtime):self.runtime=runtime

    def factory(self):
        h=self.runtime;task=h.store.current(h.session)
        roots={'runtime':h.runtime} if task is None else h._roots(task)
        if task is None:
            sprint=h.sprint_tools.overview(None)
            if sprint is not None:roots['sprint']=descendant(h.state,h.paths['sprints'])/sprint['sprint']
        return FileArtifactFactory(roots,h.cfg['batch'],descendant(h.state,h.cfg['batch']['artifact_lock']),
                                   h.cfg['limits']['lock_seconds'],h.cfg['limits']['lock_poll_seconds'])

    def packet(self,task):
        if task is None:return None
        stage=task['process']['stages'][task['stage_index']]['id']
        with self.runtime.store.transaction() as db:
            return SqliteWorkPacketRepository(db).current(
                task['id'],stage,task['iteration'],
            )

    def remember(self,task,digest):
        stage=task['process']['stages'][task['stage_index']]['id']
        with self.runtime.store.transaction() as db:
            SqliteWorkPacketRepository(db).remember(
                task['id'],stage,task['iteration'],digest,
            )
