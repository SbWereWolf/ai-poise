from pathlib import Path
from ..common import descendant, PoiseError
from ..artifacts import artifact_identity
from ..modules.artifact_factory.domain import ArtifactRecoveryIntent, ArtifactDraftIntent
from .task_paths import task_root, sprint_root, ARTIFACT_DRAFT_HISTORY_DIRECTORY
from .artifact_factory import FileArtifactFactory
from .sqlite.work_packets import SqliteWorkPacketRepository
from .file_publication import BinaryFilePublisher
from ..modules.task_delivery.domain import MaterialUseIntent


class WorkResources:
    """Composition-side file/receipt adapter, not a generic editor of task tables."""
    def __init__(self,runtime):self.runtime=runtime

    def factory(self, task=None):
        h=self.runtime
        if task is None:
            task=h.store.current(h.session)
        roots={'runtime':h.runtime} if task is None else h._roots(task)
        if task is None:
            sprint=h.sprint_tools.overview(None)
            if sprint is not None:roots['sprint']=descendant(h.state,h.paths['sprints'])/sprint['sprint']
        return FileArtifactFactory(roots, h.cfg['batch'])

    def recover_artifacts(self, request):
        h = self.runtime
        intent = ArtifactRecoveryIntent.parse(request, h.cfg['batch']['max_items'])
        digest = h.packet_digest(request)
        use_intent = MaterialUseIntent('recover_artifacts', intent.task_id, intent.request_id, digest)
        with h.store.unit_of_work() as uow:
            snapshot = uow.artifacts.recovery_snapshot(intent, digest)
            if 'replay' in snapshot:
                return snapshot['replay']
            if uow.ownership.snapshot(h.session).task_id is not None:
                raise PoiseError('Artifact recovery requires a taskless caller and released Task')
            token = h.delivery_tools.admit_use_in(uow, use_intent, [intent.task_id])
        try:
            roots = {'task': task_root(h.state, h.paths, intent.task_id, snapshot['sprint_id'])}
            if snapshot['sprint_id'] is not None:
                roots['sprint'] = sprint_root(h.state, h.paths, snapshot['sprint_id'])
            published = FileArtifactFactory(roots, h.cfg['batch']).recover_registered(
                snapshot['records'], intent.source_roots, snapshot['owners'])
            with h.store.unit_of_work() as uow:
                current = uow.artifacts.recovery_snapshot(intent, digest)
                if 'replay' in current:
                    h.delivery_tools.complete_use_in(uow, token, accepted=True)
                    return current['replay']
                if (current != snapshot or uow.ownership.snapshot(h.session).task_id is not None):
                    raise PoiseError('Artifact recovery inputs or caller authority changed before rebind')
                result = uow.artifacts.rebind(intent, digest, snapshot['records'], published, h.session)
                h.delivery_tools.complete_use_in(uow, token, accepted=True)
                return result
        except (PoiseError, OSError):
            with h.store.unit_of_work() as uow:
                replay = uow.artifacts.recovery_request(intent.task_id, intent.request_id, digest)
                if replay is not None:
                    h.delivery_tools.complete_use_in(uow, token, accepted=True)
                    return replay
                h.delivery_tools.complete_use_in(uow, token, accepted=False)
            raise

    @staticmethod
    def _snapshot_file(source: Path, target: Path, expected_digest: str) -> bool:
        FileArtifactFactory._without_links(source, 'Artifact draft source')
        FileArtifactFactory._without_links(target.parent.parent, 'Artifact draft history')
        FileArtifactFactory._without_links(target.parent, 'Artifact draft history')
        if FileArtifactFactory._read_registered(source, 'Artifact draft source') != expected_digest:
            raise PoiseError('Artifact draft source digest changed before snapshot')
        if target.exists():
            source_stat=source.stat();target_stat=target.stat()
            if ((source_stat.st_dev,source_stat.st_ino)==(target_stat.st_dev,target_stat.st_ino)
                    or target_stat.st_nlink != 1):
                raise PoiseError('Artifact draft snapshot must be an independent non-hardlinked copy')
            if FileArtifactFactory._read_registered(target, 'Artifact draft snapshot') != expected_digest:
                raise PoiseError('Artifact draft snapshot conflict')
            return False
        BinaryFilePublisher.publish(source, target, expected_digest, 0o600)
        source_stat, target_stat = source.stat(), target.stat()
        if ((source_stat.st_dev, source_stat.st_ino) == (target_stat.st_dev, target_stat.st_ino)
                or target_stat.st_nlink != 1):
            raise PoiseError('Artifact draft snapshot must be an independent non-hardlinked copy')
        return True

    def artifact_drafts(self, request):
        return self._artifact_drafts(request)

    def _artifact_drafts(self, request):
        h=self.runtime
        intent=ArtifactDraftIntent.parse(request,h.cfg['batch']['max_items'])
        request_digest=h.packet_digest(request)
        with h.store.unit_of_work() as uow:
            registry = uow.artifacts
            replay = registry.draft_request(intent.task_id, intent.request_id, request_digest)
            if replay is not None:
                return replay
            state = uow.tasks.load_state(intent.task_id)
            context = uow.tasks.restart_context(intent.task_id)
            task = {'id': state.task_id, 'status': state.status, 'claimed_by': state.claimed_by,
                    'version': state.version}
            if state.version != intent.expected_version:
                raise PoiseError('Artifact draft Task version changed')
            caller_task = uow.ownership.snapshot(h.session).task_id
            root = task_root(h.state, h.paths, intent.task_id, context['sprint_id'])
            artifact_root = root / h.cfg['batch']['artifact_directories']['task']
            drafts = registry.drafts(intent.task_id)
            records = {item['id']: item for item in registry.records(intent.task_id)}
            if intent.action == 'declare':
                if state.claimed_by != h.session or state.status != 'active':
                    raise PoiseError('Artifact draft declaration requires the active Task owner')
                token = None
            else:
                if state.claimed_by is not None or caller_task is not None:
                    raise PoiseError('Artifact draft decision requires a released Task and taskless caller')
                if state.status in ('completed', 'cancelled'):
                    raise PoiseError(f"Artifact draft lifecycle cannot mutate terminal {state.status} Task")
                token = h.delivery_tools.admit_use_in(uow, MaterialUseIntent(
                    'artifact_drafts', intent.task_id, intent.request_id, request_digest), [intent.task_id])
        links, events = [], []
        selected_ids = ({item['id'] for item in intent.artifacts} if intent.action == 'recover'
                        else set(intent.artifact_ids) if intent.action != 'declare' else set())

        def event(name, value):
            events.append((name, value))

        def finish(result):
            # The selected draft history and immutable records, not another Task's
            # filesystem work, arbitrate this decision.
            with h.store.unit_of_work() as uow:
                registry = uow.artifacts
                replay = registry.draft_request(intent.task_id, intent.request_id, request_digest)
                if replay is not None:
                    if token is not None:
                        h.delivery_tools.complete_use_in(uow, token, accepted=True)
                    return replay
                current = uow.tasks.load_state(intent.task_id)
                if (current.version != state.version or current.claimed_by != state.claimed_by
                        or uow.ownership.snapshot(h.session).task_id != caller_task):
                    raise PoiseError('Artifact draft Task authority/version changed before decision')
                latest = registry.drafts(intent.task_id)
                latest_records = {r['id']: r for r in registry.records(intent.task_id)}
                compared = selected_ids | {r['artifact_id'] for r in result.get('drafts', [])}
                for identifier in compared:
                    if latest.get(identifier) != drafts.get(identifier) or latest_records.get(identifier) != records.get(identifier):
                        raise PoiseError('Selected artifact draft changed before decision; retry original request')
                # Finalize also consumes its exact historical registered snapshots.
                for identifier in selected_ids:
                    for historical in drafts.get(identifier, {}).get('snapshots', []):
                        prior = next((r for r in records.values() if r['path'] == historical['snapshot_path']), None)
                        if prior is not None and latest_records.get(prior['id']) != prior:
                            raise PoiseError('Artifact draft historical registry changed before decision')
                if links:
                    registry.link_task(intent.task_id, links)
                for name, value in events:
                    registry.append_draft_event(intent.task_id, h.session, name, value)
                if token is not None:
                    h.delivery_tools.complete_use_in(uow, token, accepted=True)
                return result
        try:
            if intent.action == 'declare':
                declared=[]
                for item in intent.drafts:
                    FileArtifactFactory._without_links(
                        artifact_root/item['path'],'Artifact draft declaration')
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
                event('artifact_draft.declared',{
                    'request_id':intent.request_id,'request_digest':request_digest,
                    'reason':intent.reason,'drafts':declared,'result':result})
                return finish(result)
            if task['claimed_by'] is not None:
                raise PoiseError('Artifact draft decision requires a released Task claim')
            if caller_task is not None:
                raise PoiseError('Artifact draft decision requires a taskless caller')
            if task['status'] in ('completed','cancelled'):
                raise PoiseError(f"Artifact draft lifecycle cannot mutate terminal {task['status']} Task")
            if intent.action == 'recover':
                snapshots=[]
                for target in intent.artifacts:
                    record=records.get(target['id'])
                    if record is None:
                        raise PoiseError('Artifact draft recovery target is not registered')
                    expected_path=artifact_root/target['path']
                    FileArtifactFactory._without_links(
                        expected_path,'Artifact draft recovery target')
                    if (record['scope'] != target['scope'] or record['owner'] != intent.task_id
                            or record['path'] != str(expected_path)):
                        raise PoiseError('Artifact draft recovery target scope/path/identity mismatch')
                    if target['id'] in drafts:
                        raise PoiseError('Artifact draft recovery target already has lifecycle state')
                    if FileArtifactFactory._read_registered(
                        Path(record['path']),'Artifact draft recovery source') != record['digest']:
                        raise PoiseError('Artifact draft recovery source digest conflict')
                    snapshot=root/ARTIFACT_DRAFT_HISTORY_DIRECTORY/target['id']/(
                        f"0000-{record['digest']}.snapshot")
                    self._snapshot_file(Path(record['path']),snapshot,record['digest'])
                    snapshot_relative=snapshot.relative_to(root).as_posix()
                    links.extend([{
                        'id':artifact_identity('task',intent.task_id,snapshot_relative),
                        'owner':intent.task_id,'scope':'task','path':str(snapshot),
                        'relative_path':snapshot_relative,'digest':record['digest'],
                    }])
                    snapshots.append({'artifact_id':target['id'],'digest':record['digest'],
                                      'snapshot_path':str(snapshot)})
                result={'status':'artifact_drafts_recovered','task':intent.task_id,
                        'request_id':intent.request_id,
                        'artifact_ids':[item['id'] for item in intent.artifacts],
                        'snapshots':snapshots}
                for target,snapshot in zip(intent.artifacts,snapshots,strict=True):
                    event('artifact_draft.recovered',{
                        'request_id':intent.request_id,'request_digest':request_digest,
                        'reason':intent.reason,'authorization':intent.authorization,
                        'artifact_id':target['id'],'scope':target['scope'],'path':target['path'],
                        'baseline_digest':snapshot['digest'],
                        'snapshot_path':snapshot['snapshot_path'],'result':result})
                return finish(result)
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
                    self._snapshot_file(source,snapshot,record['digest'])
                    snapshot_relative=snapshot.relative_to(root).as_posix()
                    links.extend([{
                        'id':artifact_identity('task',intent.task_id,snapshot_relative),
                        'owner':intent.task_id,'scope':'task','path':str(snapshot),
                        'relative_path':snapshot_relative,'digest':record['digest'],
                    }])
                    snapshots.append({'artifact_id':record['id'],'digest':record['digest'],
                                      'snapshot_path':str(snapshot)})
                result={'status':'artifact_drafts_reviewed','task':intent.task_id,
                        'request_id':intent.request_id,'snapshots':snapshots,
                        'authorization':intent.authorization}
                for snapshot in snapshots:
                    event('artifact_draft.reviewed',{
                        'request_id':intent.request_id,'request_digest':request_digest,
                        'reason':intent.reason,'authorization':intent.authorization,
                        **snapshot,'result':result})
                return finish(result)
            for draft,record,_ in selected:
                records_by_path={item['path']:item for item in records.values()}
                for historical in draft['snapshots']:
                    snapshot_path=Path(historical['snapshot_path'])
                    try:snapshot_relative=snapshot_path.relative_to(root).as_posix()
                    except ValueError as exc:
                        raise PoiseError('Artifact draft snapshot is outside Task history') from exc
                    snapshot_record=records_by_path.get(str(snapshot_path))
                    expected_id=artifact_identity('task',intent.task_id,snapshot_relative)
                    if (snapshot_record is None or snapshot_record['id'] != expected_id
                            or snapshot_record['scope'] != 'task'
                            or snapshot_record['owner'] != intent.task_id
                            or snapshot_record['digest'] != historical['digest']):
                        raise PoiseError('Artifact draft snapshot registry/history conflict')
                    if FileArtifactFactory._read_registered(
                        snapshot_path,'Artifact draft historical snapshot'
                    ) != historical['digest']:
                        raise PoiseError('Artifact draft historical snapshot digest conflict')
                if not draft['snapshots'] or draft['snapshots'][-1]['digest'] != record['digest']:
                    raise PoiseError('Artifact draft finalization requires a review of current bytes')
            result={'status':'artifact_drafts_finalized','task':intent.task_id,
                    'request_id':intent.request_id,'artifact_ids':list(intent.artifact_ids),
                    'authorization':intent.authorization}
            event('artifact_draft.finalized',{
                'request_id':intent.request_id,'request_digest':request_digest,
                'reason':intent.reason,'authorization':intent.authorization,
                'artifact_ids':list(intent.artifact_ids),'result':result})
            return finish(result)
        except (PoiseError, OSError):
            with h.store.unit_of_work() as uow:
                replay = uow.artifacts.draft_request(intent.task_id, intent.request_id, request_digest)
                if token is not None:
                    h.delivery_tools.complete_use_in(uow, token, accepted=replay is not None)
                if replay is not None:
                    return replay
            # Published immutable snapshots belong to their path, not this attempt.
            raise

    def packet(self,task):
        if task is None:return None
        stage=task['workflow']['stages'][task['stage_index']]['id']
        with self.runtime.store.transaction() as db:
            return SqliteWorkPacketRepository(db).current(
                task['id'],stage,task['iteration'],
            )
