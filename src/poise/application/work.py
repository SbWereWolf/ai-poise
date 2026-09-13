"""One declarative entry. Delegates facts/content/files to their owners."""
from copy import deepcopy
from ..modules.work.domain import parse_request,read_range
from ..modules.tasks.domain import is_terminal_task_status
from ..modules.work.ports import WorkRuntime
from ..modules.foundation.errors import PoiseError


class WorkTools:
    def __init__(self,runtime: WorkRuntime):
        self.runtime=runtime
        self.interactions=runtime.interactions
        self.resources=runtime.work_resources

    def invoke(self,packet):
        h=self.runtime
        req=parse_request(packet,h.cfg['batch']); op=req['operation'];args=req['input']
        events=self.interactions.prepare(req['messages'])
        # A user message is a cost even when subsequent task work is rejected.
        before=h.current_task()
        # Count all received user messages during current work, independently
        # of the requested tool or the supplied reason. Completed work is not
        # charged for an unrelated later query.
        bound_before = before if before is not None and not is_terminal_task_status(before['status']) else None
        self.interactions.record(events,h.session,bound_before)
        telemetry=h.telemetry.capture(
            op,
            bound_before,
            req.get('telemetry'),
            events[-1].identity if events else None,
        )
        try:
            if op=='task':out=h.task_action(args)
            elif op=='sprint':out=h.sprint_tools.apply(args)
            elif op=='transfer':out=h.transfer_tools.apply(args)
            elif op=='bootstrap':
                out=h.bootstrap(**args)
            elif op=='handoff':out=h.handoff(args)
            elif op=='recover_empty_rework':out=h.recover_empty_rework(**args)
            elif op=='recover_empty_advance':out=h.recover_empty_advance(**args)
            elif op=='advance':out=h.advance(**args)
            elif op=='recover_missing_worktree':out=h.recover_missing_worktree(**args)
            elif op=='initialize_stage_contracts':out=h.initialize_stage_contracts(**args)
            elif op=='revise_stage_contract':out=h.revise_stage_contract(**args)
            elif op=='verify':out=self._verify(args)
            elif op=='show':out=self._show(args['queries'])
            elif op=='accept':out=h.accept()
            elif op=='integrate':out=h.integration_tools.apply(args)
            elif op=='cleanup':out=h.cleanup_tools.apply(args)
            elif op=='cancel':out=h.cancel(args['reason'])
            elif op=='artifacts':
                task=h.current_task()
                if task is not None and task['status']!='active':
                    raise PoiseError('Artifact creation requires active work')
                factory=self.resources.factory()
                paths=factory.materialize(factory.prepare(args['items']))
                records=h.register_artifact_paths(paths,task)
                out={'status':'artifacts_created','artifact_paths':paths,
                     'artifacts':[{'id':r['id'],'path':r['path'],'scope':r['scope']} for r in records]}
            else:raise PoiseError('Unreachable work operation')
        except Exception:
            after=h.current_task()
            self.interactions.record(events,h.session,after if after is not None and not is_terminal_task_status(after['status']) else bound_before)
            h.telemetry.complete(telemetry,after,{'status':'rejected'})
            raise
        current=h.current_task()
        self.interactions.record(events,h.session,current if current is not None and not is_terminal_task_status(current['status']) else bound_before)
        self.interactions.delivered(current,out)
        h.telemetry.complete(telemetry,current,out)
        sprint_subject = current if current is not None else before
        if (sprint_subject is not None and sprint_subject['sprint_id'] is not None
                and h.sprint_tools.known(sprint_subject['sprint_id'])
                and op in ('verify','accept','cancel')):
            out={**out,'sprint':h.sprint_tools.overview(sprint_subject['sprint_id'])}
        return {**out,'interaction':self.interactions.summary(h.report_task(out))}

    def _verify(self,args):
        h=self.runtime;data=h.current_task()
        packet_digest=h.packet_digest(args)
        if data is None:
            if args['result'] is not None or args['artifacts']:
                raise PoiseError('Taskless read-only verify takes explicit null result and no artifacts')
            return h.verify(None)
        if data['status']=='verified':
            saved=self.resources.packet(data)
            if not (args['result'] is None and not args['artifacts']) and saved!=packet_digest:
                raise PoiseError('Different result after delivery requires rework')
            return h.verify(None)
        if data['status']!='active' or args['result'] is None:
            raise PoiseError('Active verify requires an explicit result object')
        payload=deepcopy(args['result'])
        if not isinstance(payload.get('artifact_paths'),list) or any(not isinstance(p,str) or not p for p in payload['artifact_paths']):
            raise PoiseError('artifact_paths must be an explicit list')
        factory=self.resources.factory()
        prepared=factory.prepare(args['artifacts'])
        # Paths can be known before file creation; Task checks only semantic input.
        payload['artifact_paths']=list(dict.fromkeys(payload['artifact_paths']+[str(x.path) for x in prepared]))
        gate = h.validate_stage_entry(data)
        if not gate['passed']:
            raise PoiseError('stage entry requirements are no longer satisfied')
        h.validate_stage_result(data['id'],payload)
        h.validate_stage_scope(data)
        # Validate existing path-only inputs before producing any new file.
        h.validate_artifact_paths(args['result']['artifact_paths'],data)
        factory.materialize(prepared)
        out=h.verify(payload)
        if out['status']=='verified':self.resources.remember(h.current_task(),packet_digest)
        return out

    def _show(self,queries):
        h=self.runtime;items=[]
        for query in queries:
            kind=query['kind']
            if kind=='accounting':
                value=h.accounting.report({k:v for k,v in query.items() if k not in ('id','kind')})
                value={**value,'telemetry':h.accounting.telemetry_summary(h.telemetry.summary())}
            elif kind=='tool_result':value=h.show_output(query['receipt_id'],query['representation'],query['range'])
            elif kind=='sprint':value=h.sprint_tools.query(query['sprint_id'],query['view'])
            elif kind=='work_overview':
                sprint_statuses=query['sprint_statuses'];task_statuses=query['standalone_task_statuses']
                sprints=h.sprint_tools.overviews();standalone=h.task_queries.standalone_summary()
                value={'sprints':sprints if sprint_statuses is None else [x for x in sprints if x['status'] in sprint_statuses],
                       'standalone_tasks':standalone if task_statuses is None else [x for x in standalone if x['status'] in task_statuses]}
            elif kind=='task':value=h.show()
            elif kind=='integration':value=h.integration_tools.query(query['task_id'],query['request_id'])
            elif kind=='task_cleanup':value=h.cleanup_tools.query(query['task_id'],query['request_id'])
            elif kind=='messages':value=self.interactions.summary(h.current_task())
            elif kind=='content':value=h.show_content()
            elif kind=='evidence':value=h.show_evidence()
            elif kind=='verification_registry':value=h.show_verification_registry()
            elif kind=='trace':value=h.show_trace(query['route'],query['point'],query['submission'])
            elif kind=='section':
                value=h.show_section(query['name'],query['stage'],query['submission'])
                value={k:v for k,v in value.items() if k!='content'} | read_range(value['content'],query['range'],h.cfg['batch']['initial_read_lines'])
            else:raise PoiseError('Unreachable query kind')
            items.append({'id':query['id'],'value':value})
        return {'status':'read_only','results':items}
