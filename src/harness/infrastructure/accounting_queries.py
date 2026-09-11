"""Indexed-owner accounting projections, explicit periods and unknown-value handling."""
from __future__ import annotations
import json
from datetime import datetime,timedelta,time,timezone
from zoneinfo import ZoneInfo
from ..common import HarnessError,exact_keys
from .sqlite.accounting import timestamp,binding


class AccountingQueries:
    def __init__(self,repository,policy,project):
        self.repo,self.policy,self.project=repository,policy.data,project
        self.zone=ZoneInfo(self.policy['timezone'])

    def report(self,query):
        exact_keys(query,{'scope','group_by','from','to'},'accounting query')
        scope=query['scope'];exact_keys(scope,{'kind','id'},'accounting scope')
        if scope['kind'] not in ('all','project','task','sprint'):raise HarnessError('Unknown accounting scope')
        if scope['kind']=='all':
            if scope['id'] is not None:raise HarnessError('all scope id must be null')
        elif not isinstance(scope['id'],str) or not scope['id']:raise HarnessError('Explicit scope ID required')
        grouping=query['group_by']
        if not isinstance(grouping,list) or len(set(grouping))!=len(grouping) or set(grouping)-{'task','sprint','project','goal_type','day','week'}:
            raise HarnessError('Explicit supported accounting group dimensions required')
        start=None if query['from'] is None else timestamp(query['from'])
        end=None if query['to'] is None else timestamp(query['to'])
        if start is not None and end is not None and start>=end:raise HarnessError('Accounting period is half-open and must be ordered')
        data=self.repo.snapshot();tasks=data['tasks'];facts=[]
        def include(b):
            return scope['kind']=='all' or (self.project if scope['kind']=='project' else b[scope['kind']])==scope['id']
        def in_period(at):return (start is None or at>=start) and (end is None or at<end)
        def emit(kind,b,at,payload):
            if include(b) and in_period(at):facts.append({'kind':kind,'binding':b,'at':at,'data':payload})
        for row in data['usage']:
            d=json.loads(row['data'])
            if d['sample']['mode']!='baseline':emit('usage',d['binding'],timestamp(row['occurred_at']),d)
        open_cycles=[];unmeasured_cycles=0
        for row in data['cycles']:
            d=json.loads(row['data']);b=d['binding']
            if not include(b):continue
            if row['ended_at'] is None:open_cycles.append(row['id']);continue
            if d['kind']=='tool_cycle':
                timing=d.get('timing')
                if timing is not None and timing.get('version')==2:
                    if timing.get('status')!='measured' or timing.get('elapsed_microseconds') is None:
                        unmeasured_cycles+=1;continue
                    seconds=timing['elapsed_microseconds']/1_000_000
                elif isinstance(d.get('seconds'),(int,float)) and not isinstance(d.get('seconds'),bool):
                    seconds=d['seconds']
                else:
                    unmeasured_cycles+=1;continue
                emit('time',b,timestamp(row['started_at']),{**d,'seconds':seconds})
                continue
            a=timestamp(row['started_at']);z=timestamp(row['ended_at'])
            if start is not None:a=max(a,start)
            if end is not None:z=min(z,end)
            if a>=z:continue
            # Split at actual local midnights (DST days need not have 24 hours).
            while a<z:
                next_date=a.astimezone(self.zone).date()+timedelta(days=1)
                midnight=datetime.combine(next_date,time(),self.zone).astimezone(timezone.utc)
                until=min(z,midnight)
                facts.append({'kind':'time','binding':b,'at':a,'data':{**d,'seconds':(until-a).total_seconds(),'start':a,'end':until}})
                a=until
        missing_message_dates=0
        for row in data['messages']:
            d=json.loads(row['data']);b={k:row[v] for k,v in (('task','task_id'),('sprint','sprint_id'),('goal_type','goal_type'),('stage','stage'),('iteration','iteration'))}
            # No guessing original time: received_at is explicit provenance in reports.
            if d['occurred_at'] is None:missing_message_dates+=1
            at=timestamp(d['occurred_at'] if d['occurred_at'] is not None else row['received_at'])
            emit('message',b,at,{'id':row['id'],'reason':d['reason'],'date_source':'received' if d['occurred_at'] is None else 'event'})
        latest={}
        for row in data['credits']:
            d=json.loads(row['data']);latest[row['task_id']]=d
            emit('credit',d['binding'],timestamp(row['at']),d)
        current=[(tid,d) for tid,d in latest.items() if include(d['binding'])]
        selected_tasks={tid:t for tid,t in tasks.items() if include(binding(t))}
        totals=self._sum(facts,tasks)
        if unmeasured_cycles:totals['time_coverage']='partial'
        if start is None and end is None:
            totals['benefit']=self._current_benefit(current)
        totals['open_work_cycles']=len(open_cycles)
        totals['outcomes']={k:sum(t['status']==k for t in selected_tasks.values()) for k in ('completed','cancelled')}
        totals['unmeasured_completed_tasks']=sum(t['status']=='completed' and tid not in latest for tid,t in selected_tasks.items())
        if totals['unmeasured_completed_tasks']:
            totals['benefit']['coverage']='partial'
        totals['unfinished_tasks']=sum(t['status'] not in ('completed','cancelled') for t in selected_tasks.values())
        groups={}
        for fact in facts:
            key=tuple(self._dimension(k,fact) for k in grouping)
            groups.setdefault(key,[]).append(fact)
        groups_out=[]
        if grouping:
            for key,items in sorted(groups.items(),key=lambda x:str(x[0])):
                groups_out.append({'dimensions':dict(zip(grouping,key)),**self._sum(items,tasks)})
        reports=data['reports'];quality=data['quality'];workflows=data['workflows']
        task_rows=[]
        for tid,t in sorted(selected_tasks.items()):
            feedback=workflows[tid]['feedback'];fs=feedback['findings'];res=feedback['resolutions']
            accepted={d['resolution_id'] for d in feedback['decisions'] if d['decision']=='accepted'}
            fixed={r['finding_id'] for r in res if r['id'] in accepted}
            counted={q['finding_id'] for q in quality if q['task_id']==tid}
            delivered=[r for r in reports if r['task_id']==tid]
            task_rows.append({'task':tid,'sprint':t['sprint_id'],'goal_type':t['contract']['goal_type'],'outcome':t['status'],
                'planned_content_stages_count':sum(s['handler']!='publish' for s in t['process']['stages']),
                'delivered_stages_count':len({r['stage'] for r in delivered}),'delivered_iterations_count':len(delivered),
                'quality_findings_count':len(counted),'resolved_quality_findings_count':len(counted&fixed),
                'unclassified_inspection_records':len(fs)-len(counted),'proposal_count':len(res),
                'rejected_resolution_count':sum(d['decision']=='rejected' for d in feedback['decisions']),
                'benefit':latest[tid]['measurement'] if tid in latest else None})
        return {'status':'read_only','scope':scope,'period':{'from':query['from'],'to':query['to'],'timezone':self.policy['timezone'],'week_start':self.policy['week_start']},
                'totals':totals,'groups':groups_out,'tasks':task_rows,
                'observation':{'tokens':'source events only; no estimate or billing conversion',
                  'time':'closed tool cycles or explicit reported intervals; open/crashed cycles not guessed',
                  'messages':'observed distinct IDs; no tool-call conversion','messages_without_source_time':missing_message_dates,
                  'delivery':'tool_result_returned','benefit':'current credit without a period; net credit deltas inside a period',
                  'global':'this store, not a federation of unsynchronised stores'}}

    def _dimension(self,key,fact):
        if key=='project':return self.project
        if key in ('task','sprint','goal_type'):return fact['binding'][key]
        day=fact['at'].astimezone(self.zone).date()
        if key=='week':day-=timedelta(days=(day.weekday()-self.policy['week_start'])%7)
        return day.isoformat()

    @staticmethod
    def _current_benefit(current):
        result={'changed_lines':0,'changed_bytes':0,'changed_tokens':0,'categories':{},'coverage':'complete'}
        for tid,d in current:
            m=d['measurement']
            for k in ('changed_lines','changed_bytes','changed_tokens'):
                result[k]=None if result[k] is None or m[k] is None else result[k]+m[k]
            if m['coverage']!='complete':result['coverage']='partial'
            for c,v in m['categories'].items():
                out=result['categories'].setdefault(c,{'useful':v['useful'],'added_lines':0,'removed_lines':0,'added_bytes':0,'removed_bytes':0})
                if out['useful']!=v['useful']:out['useful']='mixed_goal_types'
                for k in ('added_lines','removed_lines','added_bytes','removed_bytes'):out[k]+=v[k]
        return result

    def _sum(self,facts,tasks):
        usage=[f for f in facts if f['kind']=='usage'];times=[f for f in facts if f['kind']=='time'];msgs=[f for f in facts if f['kind']=='message']
        cancelled=lambda f:f['binding']['task'] in tasks and tasks[f['binding']['task']]['status']=='cancelled'
        observed=sum(f['data']['contribution']['total_tokens'] for f in usage)
        result={'model_tokens':observed if usage else None,'observed_model_tokens':observed,
                'token_coverage':'partial' if usage else 'unavailable',
                'active_seconds':sum(f['data']['seconds'] for f in times) if times else None,
                'time_coverage':'partial' if times else 'unavailable','user_messages_count':len(msgs) if msgs else None,
                'observed_messages_count':len(msgs),'message_coverage':'partial' if msgs else 'unavailable',
                'cancelled_tokens':sum(f['data']['contribution']['total_tokens'] for f in usage if cancelled(f)),
                'cancelled_seconds':sum(f['data']['seconds'] for f in times if cancelled(f)),
                'unfinished_tokens':sum(f['data']['contribution']['total_tokens'] for f in usage if f['binding']['task'] in tasks and tasks[f['binding']['task']]['status'] not in ('completed','cancelled')),
                'by_cause':{}}
        for counter in ('input_tokens','output_tokens','cached_input_tokens','reasoning_tokens'):
            values=[f['data']['contribution'][counter] for f in usage]
            result[counter]=sum(values) if values and all(v is not None for v in values) else None
        intervals=sorted((f['data']['start'],f['data']['end']) for f in times if 'start' in f['data']);merged=[]
        for a,b in intervals:
            if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
            else:merged.append((a,b))
        result['wall_active_seconds']=sum((b-a).total_seconds() for a,b in merged) if intervals else None
        for f in usage+times:
            name=f['data']['cause'] if f['data']['cause'] is not None else 'unclassified'
            bucket=result['by_cause'].setdefault(name,{'model_tokens':0,'active_seconds':0})
            if f['kind']=='usage':bucket['model_tokens']+=f['data']['contribution']['total_tokens']
            else:bucket['active_seconds']+=f['data']['seconds']
        groups={}
        for f in usage+times:
            ids=tuple(sorted(f['data']['finding_ids']))
            if not ids:continue
            k=(f['binding']['task'],ids)
            g=groups.setdefault(k,{'task':f['binding']['task'],'finding_ids':list(ids),'model_tokens':0,'active_seconds':0})
            if f['kind']=='usage':g['model_tokens']+=f['data']['contribution']['total_tokens']
            else:g['active_seconds']+=f['data']['seconds']
        result['remediation_groups']=list(groups.values())
        credits=[f for f in facts if f['kind']=='credit']
        benefit={'changed_lines':0,'changed_bytes':0,'changed_tokens':0,'categories':{},'coverage':'complete'}
        for f in credits:
            for k in ('changed_lines','changed_bytes','changed_tokens'):
                value=f['data']['delta'][k];benefit[k]=None if benefit[k] is None or value is None else benefit[k]+value
        result['benefit']=benefit
        result['useful_bytes_per_1000_model_tokens']=(1000*benefit['changed_bytes']/observed) if observed and benefit['changed_bytes'] is not None else None
        result['useful_tokens_per_1000_model_tokens']=(1000*benefit['changed_tokens']/observed) if observed and benefit['changed_tokens'] is not None else None
        return result
