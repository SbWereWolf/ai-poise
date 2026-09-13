from __future__ import annotations
import json
from ...modules.foundation.errors import PoiseError


class TaskQueries:
    """Queries never mutate the aggregate or load its full section history."""
    def __init__(self, database):
        self.database = database

    def resolve_path(self, task_id, path):
        from .transfer_records import relocate_path
        with self.database.transaction() as db:
            row=db.execute('SELECT data FROM transfer_locations WHERE task_id=?',(task_id,)).fetchone()
            return path if row is None else relocate_path(path,json.loads(row['data']))

    def section(self, task_id: str, stage_id: str, section_id: str, submission_id: int | None) -> dict:
        with self.database.transaction() as db:
            sql = ("SELECT s.seq,s.iteration,l.content,l.content_state FROM section_layers l "
                   "JOIN submissions s ON s.seq=l.submission_id AND s.task_id=l.task_id "
                   "WHERE s.task_id=? AND s.stage=? AND l.section_id=?")
            params: list = [task_id, stage_id, section_id]
            if submission_id is not None:
                if type(submission_id) is not int or submission_id < 1:
                    raise PoiseError("Некорректный ID submission")
                sql += " AND s.seq=?"
                params.append(submission_id)
            row = db.execute(sql + " ORDER BY s.seq DESC LIMIT 1", params).fetchone()
            if row is None:
                raise PoiseError("Секция не найдена в указанной задаче/этапе/слое")
            return {"task":task_id,"stage":stage_id,"section":section_id,"submission":row["seq"],
                    "iteration":row["iteration"],"content":row["content"],"state":row["content_state"]}

    def content(self, task_id: str) -> dict:
        from .tasks import SqliteTaskRepository
        with self.database.transaction() as db:
            task = SqliteTaskRepository(db).load(task_id)
            return task.content_policy.describe(task.stage.stage_id, task.content_snapshot)

    def trace_point(self, task_id: str, route_id: str, point_id: str, submission_id: int | None) -> dict:
        with self.database.transaction() as db:
            sql = "SELECT submission_id,data FROM trace_point_layers WHERE task_id=? AND route_id=? AND point_id=?"
            params = [task_id,route_id,point_id]
            if submission_id is not None:
                if type(submission_id) is not int or submission_id < 1:
                    raise PoiseError("Некорректный ID submission")
                sql += " AND submission_id<=?"
                params.append(submission_id)
            row = db.execute(sql + " ORDER BY submission_id DESC LIMIT 1", params).fetchone()
            if row is None:
                raise PoiseError("Точка трассировки не найдена")
            return {"task":task_id,"route":route_id,"point":point_id,"submission":row[0],"value":json.loads(row[1])}

    def latest_submission(self, task_id, stage, iteration):
        with self.database.transaction() as db:
            row=db.execute("SELECT seq,data FROM submissions WHERE task_id=? AND stage=? AND iteration=? ORDER BY seq DESC LIMIT 1",(task_id,stage,iteration)).fetchone()
            if row is None:return None
            envelope=json.loads(row['data'])
            envelope['sections']={r['section_id']:r['content'] for r in db.execute(
                "SELECT section_id,content FROM section_layers WHERE task_id=? AND submission_id=?",(task_id,row['seq']))}
            rowmap=db.execute('SELECT data FROM transfer_locations WHERE task_id=?',(task_id,)).fetchone()
            if rowmap is not None:
                from .transfer_records import relocate_path
                envelope['artifact_paths']=[relocate_path(p,json.loads(rowmap['data'])) for p in envelope['artifact_paths']]
            return envelope

    def result(self, task_id: str, submission_id: int) -> dict:
        with self.database.transaction() as db:
            row=db.execute("SELECT data FROM task_results WHERE task_id=? AND submission_id=?",(task_id,submission_id)).fetchone()
            if row is None:
                raise PoiseError("Проверенный результат не найден")
            result=json.loads(row[0])
            locations=db.execute('SELECT data FROM transfer_locations WHERE task_id=?',(task_id,)).fetchone()
            if locations is not None:
                from .transfer_records import relocate_receipt
                result=relocate_receipt(result,json.loads(locations['data']))
            return result

    def record(self, task_id: str) -> dict | None:
        with self.database.transaction() as db:
            return self.record_in(db, task_id)

    @staticmethod
    def record_in(db, task_id: str) -> dict | None:
        row=db.execute("SELECT t.*, e.data AS execution, e.version AS execution_version FROM tasks t LEFT JOIN task_execution e ON e.task_id=t.id WHERE t.id=?",(task_id,)).fetchone()
        if row is None: return None
        # Transitional DTO for the existing runner. Lifecycle fields are read-only here.
        metadata = json.loads(row['metadata'])
        history = [json.loads(item[0]) for item in db.execute(
            "SELECT data FROM task_events WHERE task_id=? ORDER BY seq", (task_id,)
        )]
        if row['status'] == 'newborn':
            from ...modules.tasks.newborn import NewbornTask
            newborn = NewbornTask.restore(
                row['id'], row['claimed_by'], row['version'], metadata
            )
            return {
                **metadata,
                **newborn.describe(),
                'id':row['id'],
                'sprint_id':newborn.sprint_id,
                'stage_index':0,
                'iteration':1,
                '_version':row['version'],
                '_execution_version':None,
                'worktree':None,
                'branch':None,
                'base':None,
                'attempts':0,
                'publication':None,
                'pending':None,
                'entry_tree':None,
                'last_report':None,
                'result_commit':None,
                'history':history,
            }
        from .tasks import without_retired_method_timeout
        items = [without_retired_method_timeout(json.loads(r[0])) for r in db.execute(
            'SELECT data FROM task_methods WHERE task_id=? ORDER BY rowid', (task_id,)
        )]
        metadata['contract']['methods'] = [item['method'] for item in items]
        metadata['contract']['checks'] = {s['id']:[item['method']['id'] for item in items if s['id'] in item['stages']]
                                          for s in metadata['process']['stages']}
        execution = ({"worktree":None,"branch":None,"base":None,"attempts":0,"publication":None,
                      "pending":None,"entry_tree":None,"last_report":None} if row["execution"] is None else json.loads(row["execution"]))
        rowmap=db.execute('SELECT data FROM transfer_locations WHERE task_id=?',(task_id,)).fetchone()
        if rowmap is not None:
            from .transfer_records import relocate_receipt
            execution=relocate_receipt(execution,json.loads(rowmap['data']))
        report = execution["last_report"]
        return {**metadata, **execution, "id":row["id"],
                "status":row["status"],"stage_index":row["stage_index"],"iteration":row["iteration"],
                "claimed_by":row["claimed_by"],"result_commit":None if report is None else report["commit"],
                "_version":row["version"],"_execution_version":row["execution_version"],
                "history":history}

    def history(self, task_id: str) -> list[dict]:
        with self.database.transaction() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM task_events WHERE task_id=? ORDER BY seq",(task_id,))]

    def summary(self) -> list[dict]:
        with self.database.transaction() as db:
            return [{"id":r["id"],"status":r["status"],"goal":json.loads(r["metadata"])["goal"]}
                    for r in db.execute("SELECT id,status,metadata FROM tasks ORDER BY id")]

    def standalone_summary(self) -> list[dict]:
        with self.database.transaction() as db:
            result=[]
            for row in db.execute("SELECT id,status,metadata FROM tasks ORDER BY id"):
                metadata=json.loads(row["metadata"])
                if metadata["sprint_id"] is None:
                    result.append({"id":row["id"],"status":row["status"],"goal":metadata["goal"]})
            return result

    def evidence_view(self, task_id):
        from .tasks import SqliteTaskRepository
        with self.database.transaction() as db:
            result=SqliteTaskRepository(db).load(task_id).evidence_context()
            locations=db.execute('SELECT data FROM transfer_locations WHERE task_id=?',(task_id,)).fetchone()
            if locations is not None:
                from .transfer_records import relocate_receipt
                result=relocate_receipt(result,json.loads(locations['data']))
            return {'status':'read_only','task':task_id,**result}

    def verification_registry(self, task_id):
        from .tasks import SqliteTaskRepository
        with self.database.transaction() as db:
            registry = SqliteTaskRepository(db).load(task_id).check_registry
            return {
                'status': 'read_only',
                'task': task_id,
                'revision': registry.revision,
                'executable_obligations': list(registry.executable_obligations),
                'current': [entry.to_dict() for entry in registry.entries],
                'history': [snapshot.to_dict() for snapshot in registry.history],
                'requests': [request.to_dict() for request in registry.requests],
            }
