from pathlib import Path
from ..common import descendant, PoiseError
from ..modules.artifact_factory.domain import ArtifactRecoveryIntent
from .task_paths import task_root, sprint_root
from .sqlite.artifacts import SqliteArtifactRepository
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

    def recover_artifacts(self, request):
        h = self.runtime
        intent = ArtifactRecoveryIntent.parse(request,h.cfg['batch']['max_items'])
        digest = h.packet_digest(request)
        with h.store.transaction() as db:
            registry = SqliteArtifactRepository(db)
            snapshot = registry.recovery_snapshot(intent,digest)
            if 'replay' in snapshot:
                return snapshot['replay']
            if db.execute('SELECT id FROM tasks WHERE claimed_by=? LIMIT 1',(h.session,)).fetchone() is not None:
                raise PoiseError('Artifact recovery requires a taskless caller and released Task')
            roots = {'task':task_root(h.state,h.paths,intent.task_id,snapshot['sprint_id'])}
            if snapshot['sprint_id'] is not None:
                roots['sprint'] = sprint_root(h.state,h.paths,snapshot['sprint_id'])
            factory = FileArtifactFactory(roots,h.cfg['batch'],
                descendant(h.state,h.cfg['batch']['artifact_lock']),
                h.cfg['limits']['lock_seconds'],h.cfg['limits']['lock_poll_seconds'])
            published = factory.recover_registered(snapshot['records'],intent.source_roots,snapshot['owners'])
            return registry.rebind(intent,digest,snapshot['records'],published,h.session)

    def packet(self,task):
        if task is None:return None
        stage=task['process']['stages'][task['stage_index']]['id']
        with self.runtime.store.transaction() as db:
            return SqliteWorkPacketRepository(db).current(
                task['id'],stage,task['iteration'],
            )
