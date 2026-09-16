"""First primary binding per observed user turn; retry cannot move prior cost."""
import json
from datetime import datetime,timezone
from ...modules.interactions.domain import UserMessage,InteractionLedger
from ...common import PoiseError
from ...modules.workflow.domain import HandlerKind


class InteractionStore:
    def __init__(self,database,project,config):
        self.database,self.project,self.config=database,project,config

    def prepare(self,raw):
        prepared=tuple(UserMessage.parse(m,self.config['message_source'],self.config['message_reasons']) for m in raw)
        for message in prepared:
            stamp=json.loads(message.data)['occurred_at']
            if stamp is not None:
                try:
                    parsed=datetime.fromisoformat(stamp)
                    if parsed.tzinfo is None:raise ValueError('timezone required')
                except ValueError as exc:raise PoiseError('occurred_at requires ISO timestamp with timezone') from exc
        InteractionLedger.merge({},prepared)
        return prepared

    def record(self,prepared,session,task):
        with self.database.transaction() as db:
            existing={}
            for event in prepared:
                row=db.execute('SELECT data FROM interaction_events WHERE id=?',(event.identity,)).fetchone()
                if row is not None:existing[event.identity]=row[0]
            InteractionLedger.merge(existing,prepared)
            for event in prepared:
                db.execute('INSERT OR IGNORE INTO interaction_events(id,project,data,received_at) VALUES(?,?,?,?)',
                           (event.identity,self.project,event.data,datetime.now(timezone.utc).isoformat()))
                if task is not None:
                    row=db.execute('SELECT task_id FROM interaction_bindings WHERE event_id=?',(event.identity,)).fetchone()
                    if row is None:
                        newborn = task['status'] == 'newborn'
                        stage = ('newborn' if newborn else
                                 task['process']['stages'][task['stage_index']]['id'])
                        goal_type = (task.get('goal_type') or 'newborn' if newborn
                                     else task['contract']['goal_type'])
                        db.execute('INSERT INTO interaction_bindings VALUES(?,?,?,?,?,?,?,?)',
                            (event.identity,task['id'],task['sprint_id'],goal_type,stage,task['iteration'],session,self.project))

    def delivered(self,task,report):
        # This records tool-result delivery, not an unobservable final ChatGPT answer.
        if task is None or report['status']!='verified':return
        if report['handler']==HandlerKind.PUBLISH.value:return
        with self.database.transaction() as db:
            db.execute('INSERT OR IGNORE INTO interaction_reports VALUES(?,?,?,?,?)',
                       (task['id'],report['stage'],report['iteration'],datetime.now(timezone.utc).isoformat(),'tool_result_returned'))

    def summary(self,task):
        with self.database.transaction() as db:
            if task is None:
                rows=db.execute('SELECT e.data FROM interaction_events e WHERE project=?',(self.project,)).fetchall()
                delivered=[]
            else:
                rows=db.execute('SELECT e.data FROM interaction_events e JOIN interaction_bindings b ON b.event_id=e.id WHERE b.task_id=?',(task['id'],)).fetchall()
                delivered=db.execute('SELECT stage,iteration FROM interaction_reports WHERE task_id=?',(task['id'],)).fetchall()
            counts={name:0 for name in self.config['message_reasons']};unknown=0
            for row in rows:
                event=json.loads(row[0])
                if event['reason'] is None:unknown+=1
                else:counts[event['reason']]+=1
            newborn = task is not None and task['status'] == 'newborn'
            return {'user_messages_count':len(rows),'observed_messages_count':len(rows),
                    'coverage':'partial' if rows else 'unavailable',
                    'source':self.config['message_source'], 'reason_counts':counts,'unclassified_messages':unknown,
                    'task':None if task is None else task['id'],
                    'goal_type':None if task is None else (task.get('goal_type') if newborn else task['contract']['goal_type']),
                    'outcome':None if task is None else task['status'],
                    'planned_content_stages_count':None if task is None or newborn else sum(s['handler']!=HandlerKind.PUBLISH.value for s in task['process']['stages']),
                    'delivered_stages_count':len({r['stage'] for r in delivered}),
                    'delivered_iterations_count':len(delivered),
                    'delivery_observation':'tool_result_returned'}

    @staticmethod
    def observe_message(path, project, source, conversation, turn, max_bytes):
        """Locate one existing source receipt without registering or rebinding it."""
        import hashlib
        from ..diagnostic_io import readonly_database, json_record
        identity = hashlib.sha256(json.dumps(
            [source['id'], conversation, turn], ensure_ascii=False, separators=(',', ':')
        ).encode()).hexdigest()
        with readonly_database(path) as db:
            row = db.execute('SELECT substr(data,1,?) AS data,received_at FROM interaction_events WHERE id=? AND project=?',
                             (max_bytes+1, identity, project)).fetchone()
            if row is None:
                return None
            data = json_record(row['data'], max_bytes)
            if (not isinstance(data, dict) or data.get('source') != source
                    or data.get('conversation_id') != conversation or data.get('message_id') != turn):
                raise PoiseError('Interaction receipt identity mismatch')
            return {'receipt_id': identity, 'project': project, 'source': source,
                    'conversation_id': conversation, 'message_id': turn, 'received_at': row['received_at']}
