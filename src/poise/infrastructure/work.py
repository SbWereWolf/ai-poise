from pathlib import Path
import json
import os
import tempfile
from ..common import descendant, PoiseError
from ..artifacts import artifact_identity
from ..modules.artifact_factory.domain import ArtifactRecoveryIntent, ArtifactDraftIntent
from .task_paths import task_root, sprint_root, ARTIFACT_DRAFT_HISTORY_DIRECTORY
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

    @staticmethod
    def _snapshot_file(source: Path, target: Path, expected_digest: str) -> bool:
        FileArtifactFactory._without_links(source, 'Artifact draft source')
        FileArtifactFactory._without_links(target.parent.parent, 'Artifact draft history')
        if FileArtifactFactory._read_registered(source, 'Artifact draft source') != expected_digest:
            raise PoiseError('Artifact draft source digest changed before snapshot')
        if target.exists():
            if FileArtifactFactory._read_registered(target, 'Artifact draft snapshot') != expected_digest:
                raise PoiseError('Artifact draft snapshot conflict')
            return False
        target.parent.mkdir(parents=True,exist_ok=True)
        FileArtifactFactory._without_links(target.parent, 'Artifact draft history')
        fd,name=tempfile.mkstemp(dir=target.parent)
        temporary=Path(name)
        try:
            with os.fdopen(fd,'wb') as output:
                os.fchmod(output.fileno(),0o600)
                observed=FileArtifactFactory._read_registered(
                    source,'Artifact draft source',output)
                if observed != expected_digest:
                    raise PoiseError('Artifact draft source digest changed during snapshot')
                output.flush();os.fsync(output.fileno())
            os.link(temporary,target)
        finally:
            temporary.unlink(missing_ok=True)
        return True

    def artifact_drafts(self, request):
        h=self.runtime
        intent=ArtifactDraftIntent.parse(request,h.cfg['batch']['max_items'])
        request_digest=h.packet_digest(request)
        created=[]
        try:
            with h.store.transaction() as db:
                registry=SqliteArtifactRepository(db)
                replay=registry.draft_request(intent.task_id,intent.request_id,request_digest)
                if replay is not None:return replay
                task=db.execute(
                    'SELECT id,status,claimed_by,version,metadata FROM tasks WHERE id=?',
                    (intent.task_id,)).fetchone()
                if task is None:raise PoiseError('Artifact draft Task is not registered')
                if task['version'] != intent.expected_version:
                    raise PoiseError('Artifact draft Task version changed')
                metadata=json.loads(task['metadata'])
                root=task_root(h.state,h.paths,intent.task_id,metadata['sprint_id'])
                artifact_root=root/h.cfg['batch']['artifact_directories']['task']
                drafts=registry.drafts(intent.task_id)
                records={item['id']:item for item in registry.records(intent.task_id)}
                if intent.action == 'declare':
                    declared=[]
                    for item in intent.drafts:
                        registry_path=(artifact_root/item['path']).relative_to(root).as_posix()
                        identifier=artifact_identity('task',intent.task_id,registry_path)
                        if identifier in records:
                            raise PoiseError('Registered immutable artifact requires explicit recover')
                        if identifier in drafts:
                            raise PoiseError('Artifact draft path is already declared')
                        declared.append({'artifact_id':identifier,'scope':'task',
                                         'path':item['path'],'state':'open'})
                    if task['claimed_by'] != h.session or task['status'] != 'active':
                        raise PoiseError('Artifact draft declaration requires the active Task owner')
                    result={'status':'artifact_drafts_declared','task':intent.task_id,
                            'request_id':intent.request_id,'drafts':declared}
                    registry.append_draft_event(intent.task_id,h.session,'artifact_draft.declared',{
                        'request_id':intent.request_id,'request_digest':request_digest,
                        'reason':intent.reason,'drafts':declared,'result':result})
                    return result
                if task['claimed_by'] is not None:
                    raise PoiseError('Artifact draft decision requires a released Task claim')
                if db.execute('SELECT id FROM tasks WHERE claimed_by=? LIMIT 1',(h.session,)).fetchone():
                    raise PoiseError('Artifact draft decision requires a taskless caller')
                if intent.action == 'recover':
                    snapshots=[]
                    for target in intent.artifacts:
                        record=records.get(target['id'])
                        if record is None:
                            raise PoiseError('Artifact draft recovery target is not registered')
                        expected_path=(artifact_root/target['path']).resolve()
                        if (record['scope'] != target['scope'] or record['owner'] != intent.task_id
                                or Path(record['path']).resolve() != expected_path):
                            raise PoiseError('Artifact draft recovery target scope/path/identity mismatch')
                        if target['id'] in drafts:
                            raise PoiseError('Artifact draft recovery target already has lifecycle state')
                        if FileArtifactFactory._read_registered(
                            Path(record['path']),'Artifact draft recovery source') != record['digest']:
                            raise PoiseError('Artifact draft recovery source digest conflict')
                        snapshot=root/ARTIFACT_DRAFT_HISTORY_DIRECTORY/target['id']/(
                            f"0000-{record['digest']}.snapshot")
                        if self._snapshot_file(Path(record['path']),snapshot,record['digest']):
                            created.append(snapshot)
                        snapshots.append({'artifact_id':target['id'],'digest':record['digest'],
                                          'snapshot_path':str(snapshot)})
                    result={'status':'artifact_drafts_recovered','task':intent.task_id,
                            'request_id':intent.request_id,
                            'artifact_ids':[item['id'] for item in intent.artifacts],
                            'snapshots':snapshots}
                    for target,snapshot in zip(intent.artifacts,snapshots,strict=True):
                        registry.append_draft_event(intent.task_id,h.session,'artifact_draft.recovered',{
                            'request_id':intent.request_id,'request_digest':request_digest,
                            'reason':intent.reason,'authorization':intent.authorization,
                            'artifact_id':target['id'],'scope':target['scope'],'path':target['path'],
                            'baseline_digest':snapshot['digest'],
                            'snapshot_path':snapshot['snapshot_path'],'result':result})
                    return result
                selected=[]
                for identifier in intent.artifact_ids:
                    draft=drafts.get(identifier);record=records.get(identifier)
                    if draft is None or draft['state'] != 'open' or record is None:
                        raise PoiseError('Artifact draft is not open and registered')
                    if draft['digest'] != record['digest']:
                        raise PoiseError('Artifact draft registry digest is inconsistent')
                    source=Path(record['path'])
                    if FileArtifactFactory._read_registered(source,'Artifact draft source') != record['digest']:
                        raise PoiseError('Artifact draft current bytes conflict with reviewed registry')
                    selected.append((draft,record,source))
                if intent.action == 'review':
                    snapshots=[]
                    for draft,record,source in selected:
                        revision=len(draft['snapshots'])+1
                        snapshot=root/ARTIFACT_DRAFT_HISTORY_DIRECTORY/record['id']/(
                            f"{revision:04d}-{record['digest']}.snapshot")
                        if self._snapshot_file(source,snapshot,record['digest']):created.append(snapshot)
                        snapshots.append({'artifact_id':record['id'],'digest':record['digest'],
                                          'snapshot_path':str(snapshot)})
                    result={'status':'artifact_drafts_reviewed','task':intent.task_id,
                            'request_id':intent.request_id,'snapshots':snapshots,
                            'authorization':intent.authorization}
                    for snapshot in snapshots:
                        registry.append_draft_event(intent.task_id,h.session,'artifact_draft.reviewed',{
                            'request_id':intent.request_id,'request_digest':request_digest,
                            'reason':intent.reason,'authorization':intent.authorization,
                            **snapshot,'result':result})
                    return result
                for draft,record,_ in selected:
                    if not draft['snapshots'] or draft['snapshots'][-1]['digest'] != record['digest']:
                        raise PoiseError('Artifact draft finalization requires a review of current bytes')
                result={'status':'artifact_drafts_finalized','task':intent.task_id,
                        'request_id':intent.request_id,'artifact_ids':list(intent.artifact_ids),
                        'authorization':intent.authorization}
                registry.append_draft_event(intent.task_id,h.session,'artifact_draft.finalized',{
                    'request_id':intent.request_id,'request_digest':request_digest,
                    'reason':intent.reason,'authorization':intent.authorization,
                    'artifact_ids':list(intent.artifact_ids),'result':result})
                return result
        except Exception:
            for path in reversed(created):path.unlink(missing_ok=True)
            raise

    def packet(self,task):
        if task is None:return None
        stage=task['process']['stages'][task['stage_index']]['id']
        with self.runtime.store.transaction() as db:
            return SqliteWorkPacketRepository(db).current(
                task['id'],stage,task['iteration'],
            )
