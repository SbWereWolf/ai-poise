from pathlib import Path
from ..common import descendant, PoiseError
from .artifact_factory import FileArtifactFactory


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
            row=db.execute('SELECT digest FROM work_packets WHERE task_id=? AND stage=? AND iteration=?',
                           (task['id'],stage,task['iteration'])).fetchone()
            return None if row is None else row[0]

    def remember(self,task,digest):
        stage=task['process']['stages'][task['stage_index']]['id']
        with self.runtime.store.transaction() as db:
            row=db.execute('SELECT digest FROM work_packets WHERE task_id=? AND stage=? AND iteration=?',
                           (task['id'],stage,task['iteration'])).fetchone()
            if row is not None and row[0]!=digest:
                raise PoiseError('Verified packet cannot be replaced')
            db.execute('INSERT OR IGNORE INTO work_packets VALUES(?,?,?,?)',(task['id'],stage,task['iteration'],digest))
