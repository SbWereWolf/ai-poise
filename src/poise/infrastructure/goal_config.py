"""Single-pack publication: explicit paths, SQLite receipt and atomic file replace.

SQLite does not transact the filesystem. A persisted pending candidate is replayed
only when the configured target still has its known before/after digest.
"""
from __future__ import annotations
import json
import math
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from ..common import descendant, exact_keys
from ..modules.foundation.errors import PoiseError, VersionConflict
from ..modules.goal_config.domain import GoalTypeDefinition, fingerprint, canonical
from .locking import exclusive_lock


class PublicationPending(PoiseError):
    pass


def strict_json(text):
    def pairs(values):
        result={}
        for key, value in values:
            if key in result:
                raise PoiseError(f"Повтор поля JSON: {key}")
            result[key]=value
        return result
    def invalid_constant(value):
        raise PoiseError(f"Недопустимое JSON-число: {value}")
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise PoiseError(f"Неверный JSON: {exc}") from exc


def read_document(path):
    try:
        data=strict_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PoiseError(f"Нельзя прочитать {path}: {exc}") from exc
    if not isinstance(data,dict):
        raise PoiseError(f"{path}: нужен JSON-объект")
    return data


def atomic_write(path: Path, content: bytes, mode: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary=Path(stream.name)
            os.fchmod(stream.fileno(),mode)
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
        temporary=None
        fd=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class EditorSettings:
    def __init__(self, path):
        self.path=Path(path).resolve()
        self.raw=read_document(self.path)
        c=self.raw
        exact_keys(c,{"schema","root","database","lock","responses","file_mode","json_indent",
                      "lock_seconds","lock_poll_seconds","max_changes","max_input_bytes","output_chars",
                      "exit_codes","processes","templates"},"goal editor settings")
        if c["schema"]!="goal-config-editor-1":
            raise PoiseError("Неподдерживаемый формат редактора; нет автоматической миграции")
        if not isinstance(c["root"],str) or not c["root"]:
            raise PoiseError("root обязателен")
        self.root=(self.path.parent/c["root"]).resolve(strict=True)
        for key in ("database","lock","responses"):
            descendant(self.root,c[key])
        for key in ("lock_seconds","lock_poll_seconds"):
            if type(c[key]) not in (int,float) or not math.isfinite(c[key]) or c[key]<=0:
                raise PoiseError(f"{key}: нужен явный положительный предел")
        for key in ("max_changes","max_input_bytes","output_chars"):
            if type(c[key]) is not int or c[key]<=0:
                raise PoiseError(f"{key}: нужен положительный integer")
        if type(c["json_indent"]) is not int or c["json_indent"]<0:
            raise PoiseError("json_indent: нужен явный неотрицательный integer")
        if type(c["file_mode"]) is not int or not 0<=c["file_mode"]<=0o777:
            raise PoiseError("file_mode: нужны обычные POSIX permission bits")
        exact_keys(c["exit_codes"],{"success","rejected","pending"},"exit_codes")
        if any(type(v) is not int or not 0<=v<=255 for v in c["exit_codes"].values()) or len(set(c["exit_codes"].values()))!=3:
            raise PoiseError("exit_codes: нужны три различных явных кода")
        if not isinstance(c["processes"],dict) or not c["processes"] or not isinstance(c["templates"],dict):
            raise PoiseError("processes/templates должны быть явными mappings")
        paths=[]
        for goal, relative in c["processes"].items():
            if not isinstance(goal,str) or not goal.strip() or any(x in goal for x in "/\\\x00") or goal in (".",".."):
                raise PoiseError("Некорректный goal_type в registry")
            target=descendant(self.root,relative)
            if (self.root/relative).is_symlink():
                raise PoiseError("Редактируемый конфиг не должен быть symlink")
            paths.append(target)
        for key, value in c["templates"].items():
            exact_keys(value,{"path","version","digest"},f"template {key}")
            if not all(isinstance(value[k],str) and value[k] for k in value):
                raise PoiseError("Шаблон требует path/version/digest")
            template=descendant(self.root,value["path"])
            if template in paths:
                raise PoiseError("Шаблон и редактируемый pack должны быть независимы")
        protected=[descendant(self.root,c[k]) for k in ("database","lock","responses")]
        if len(set(paths))!=len(paths) or len(set(protected))!=len(protected) or any(p==q or p.is_relative_to(q) or q.is_relative_to(p) for p in paths for q in protected):
            raise PoiseError("Коллизия путей редактора")
        minimum_reply={"status":"rejected","response_path":str(descendant(self.root,c["responses"])/(str(uuid.uuid4())+".json"))}
        if c["output_chars"] < len(json.dumps(minimum_reply,ensure_ascii=False))+1:
            raise PoiseError("output_chars недостаточен для обязательного адреса результата; публикация не начата")

    def target(self,goal):
        if goal not in self.raw["processes"]:
            raise PoiseError(f"goal_type {goal} не зарегистрирован в editor settings")
        rel=self.raw["processes"][goal]
        if (self.root/rel).is_symlink():
            raise PoiseError("Конфиг не может быть symlink")
        return descendant(self.root,rel)


class FileTemplates:
    def __init__(self,settings):
        self.settings=settings

    def resolve(self,selection):
        exact_keys(selection,{"id","version","digest"},"template selection")
        if not all(isinstance(v,str) and v for v in selection.values()):
            raise PoiseError("Нужны явные id/version/digest шаблона")
        if selection["id"] not in self.settings.raw["templates"]:
            raise PoiseError("Шаблон не зарегистрирован")
        expected=self.settings.raw["templates"][selection["id"]]
        if (selection["version"],selection["digest"])!=(expected["version"],expected["digest"]):
            raise VersionConflict("Выбранная версия/digest шаблона не совпадает с registry")
        data=read_document(descendant(self.settings.root,expected["path"]))
        if fingerprint(data)!=expected["digest"]:
            raise VersionConflict("Содержимое шаблона изменилось; digest не совпадает")
        return GoalTypeDefinition.parse(data).data


class FileGoalConfigRepository:
    def __init__(self,settings):
        self.settings=settings

    @contextmanager
    def edit(self,goal):
        s=self.settings; cfg=s.raw
        target=s.target(goal)
        db_path=descendant(s.root,cfg["database"])
        with exclusive_lock(descendant(s.root,cfg["lock"]),cfg["lock_seconds"],cfg["lock_poll_seconds"]):
            db_path.parent.mkdir(parents=True,exist_ok=True)
            db=sqlite3.connect(db_path,timeout=0,isolation_level=None,autocommit=True)
            db.row_factory=sqlite3.Row
            try:
                version=db.execute("PRAGMA user_version").fetchone()[0]
                if version==0:
                    if db.execute("select name from sqlite_master where type='table'").fetchone():
                        raise PoiseError("Неизвестная БД редактора; миграция запрещена")
                    db.execute("BEGIN")
                    try:
                        db.execute("CREATE TABLE config_operations(request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, goal_type TEXT NOT NULL, source_revision TEXT, candidate TEXT NOT NULL, receipt TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('pending','completed')))")
                        db.execute("CREATE TABLE config_heads(goal_type TEXT PRIMARY KEY, revision TEXT NOT NULL)")
                        db.execute("CREATE INDEX config_pending ON config_operations(goal_type,phase)")
                        db.execute("PRAGMA user_version=1")
                        db.execute("COMMIT")
                    except BaseException:
                        db.execute("ROLLBACK"); raise
                elif version!=1:
                    raise PoiseError("Schema БД редактора несовместима; миграция не выполняется")
                edit=FileConfigEdit(s,db,goal,target)
                edit.recover()
                yield edit
            except sqlite3.Error as exc:
                raise PoiseError(f"Ошибка хранилища редактора: {exc}") from exc
            finally:
                db.close()


class FileConfigEdit:
    def __init__(self,settings,db,goal,target):
        self.settings,self.db,self.goal,self.target=settings,db,goal,target

    def current(self):
        if not self.target.exists():
            return None
        data=read_document(self.target)
        if data.get("goal_type")!=self.goal:
            raise PoiseError("Идентичность pack не совпадает с registry")
        return GoalTypeDefinition.parse(data).data

    def replay(self,request_id,request_digest):
        row=self.db.execute("SELECT * FROM config_operations WHERE request_id=?",(request_id,)).fetchone()
        if row is None: return None
        if row["request_digest"]!=request_digest or row["goal_type"]!=self.goal:
            raise VersionConflict("request_id уже использован с другим содержимым")
        if row["phase"]!="completed":
            raise PublicationPending("Операция ещё ожидает публикации")
        current=self.current()
        receipt=json.loads(row["receipt"])
        if receipt["config_path"] != str(self.target):
            raise VersionConflict("Путь завершённой операции отличается от текущего registry")
        return {**receipt,"replayed":True,"current_revision":None if current is None else fingerprint(current)}

    def publish(self,request,request_digest,source_revision,candidate,template):
        current=self.current()
        actual=None if current is None else fingerprint(current)
        if actual!=source_revision:
            raise VersionConflict("Конфиг изменён после чтения")
        head=self.db.execute("SELECT revision FROM config_heads WHERE goal_type=?",(self.goal,)).fetchone()
        if head is not None and actual!=head["revision"]:
            raise VersionConflict("Конфиг изменён вне редактора; требуется явное решение, автоматического принятия нет")
        revision=fingerprint(candidate)
        receipt={"status":"applied","request_id":request["request_id"],"goal_type":self.goal,
                 "revision":revision,"previous_revision":source_revision,"current_revision":revision,
                 "config_path":str(self.target),"changed":revision!=source_revision,"replayed":False,
                 "change_count":len(request["changes"]),"template":template,
                 "created_at":datetime.now(timezone.utc).isoformat()}
        self.db.execute("INSERT INTO config_operations VALUES(?,?,?,?,?,?,?)",(
            request["request_id"],request_digest,self.goal,source_revision,canonical(candidate),canonical(receipt),"pending"))
        self.recover()
        return receipt

    def recover(self):
        rows=self.db.execute("SELECT * FROM config_operations WHERE goal_type=? AND phase='pending'",(self.goal,)).fetchall()
        if len(rows)>1:
            raise PoiseError("Нарушена согласованность: несколько pending публикаций одного pack")
        for row in rows:
            if json.loads(row["receipt"])["config_path"] != str(self.target):
                raise VersionConflict("Путь pending publication изменён; исходное назначение нельзя перенаправлять")
            candidate=json.loads(row["candidate"]); revision=fingerprint(candidate)
            current=self.current(); actual=None if current is None else fingerprint(current)
            if actual not in (row["source_revision"],revision):
                raise VersionConflict("Pending publication: внешний конфиг не совпадает ни с исходным, ни с кандидатом; запись остановлена")
            if actual!=revision:
                try:
                    content=(json.dumps(candidate,ensure_ascii=False,indent=self.settings.raw["json_indent"],allow_nan=False)+"\n").encode("utf-8")
                    atomic_write(self.target,content,self.settings.raw["file_mode"])
                except OSError as exc:
                    raise PublicationPending(f"pending публикация {row['request_id']}: {exc}") from exc
            self.db.execute("BEGIN")
            try:
                self.db.execute("INSERT INTO config_heads VALUES(?,?) ON CONFLICT(goal_type) DO UPDATE SET revision=excluded.revision",(self.goal,revision))
                self.db.execute("UPDATE config_operations SET phase='completed' WHERE request_id=?",(row["request_id"],))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK"); raise
