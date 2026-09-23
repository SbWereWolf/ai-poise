"""Non-destructive database mechanics shared by distinct schema-owned adapters."""
import os
from pathlib import Path
import sqlite3
import tempfile
from environment_maintenance.repairs.sqlite_schema import shape
from environment_maintenance.shared.filesystem import lexical_path,regular_or_missing
from environment_maintenance.shared.protocol import result, RepairError
from poise.common import load_config,configured_root,configured_storage_path,PoiseError


def selected_path(config,selector,expected_state):
    root,cfg,_=load_config(lexical_path(config))
    state=lexical_path(configured_root(root,cfg['paths']['state']))
    expected = lexical_path(expected_state)
    if state != expected:
        raise RepairError('configuration_drift', 'Configured state root differs from the selected effective root')
    path = lexical_path(configured_storage_path(state,selector(cfg)))
    if not path.is_relative_to(expected):
        raise RepairError('configuration_drift', 'Configured database is outside the selected effective root')
    return path


def inspect(path,version,expected):
    if not regular_or_missing(path):return result('action_required','missing')
    if not os.access(path,os.R_OK|os.W_OK):return result('action_required','permissions')
    # Checks have no permission to checkpoint/migrate an active SQLite writer.
    if any(Path(str(path)+s).exists() for s in ('-wal','-shm','-journal')):
        return result('action_required','busy',detail='Quiescent maintenance requires no SQLite sidecars')
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0) as db:
            if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:return result('action_required','corrupt')
            if db.execute('PRAGMA user_version').fetchone()[0]!=version:return result('action_required','version')
            if shape(db)!=expected:return result('action_required','schema')
            if db.execute('PRAGMA foreign_key_check').fetchall():return result('action_required','foreign_keys')
    except sqlite3.DatabaseError as exc:return result('action_required','corrupt',detail=str(exc))
    return result('satisfied','ok')


def maintain(config,mode,selector,initialize,expected_state):
    try:path=selected_path(config,selector,expected_state)
    except PoiseError as e:return result('action_required','invalid_config',detail=str(e))
    # Build the comparison schema through the SAME owner, outside inspected state.
    with tempfile.TemporaryDirectory(prefix='poise-schema-reference-') as raw:
        reference=Path(raw)/'reference.sqlite';initialize(reference,Path(raw)/'reference.lock')
        with sqlite3.connect(reference.as_uri()+'?mode=ro',uri=True) as db:
            version=db.execute('PRAGMA user_version').fetchone()[0];expected=shape(db)
        before=inspect(path,version,expected)
        if mode!='apply' or before['code']!='missing':return before
        if not path.parent.is_dir():return result('action_required','missing_parent')
        # The actual new file is also created by the owner. link is create-only.
        with tempfile.TemporaryDirectory(prefix='.poise-db-',dir=path.parent) as work:
            new=Path(work)/'database.sqlite';initialize(new,Path(work)/'database.lock')
            checked=inspect(new,version,expected)
            if checked['status']!='satisfied':return checked
            new.chmod(0o600);os.link(new,path)
    after=inspect(path,version,expected)
    if after['status']=='satisfied':after['status']='repaired'
    return after
