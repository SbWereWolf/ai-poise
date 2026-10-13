#!/usr/bin/env python3
"""Quiescent incremental backup relative to the immutable cloud base.

Uses the public Task DB backup owner. Never edits domain databases. All files
changed since the immutable base are retained, not only the latest stage delta.
"""
import argparse, contextlib, gzip, hashlib, json, os, pathlib, shutil, sqlite3, subprocess, tarfile
P=pathlib.Path
ROOT=P(os.environ.get('POISE_RECOVERY_ROOT','/home/sbwerewolf/workdata')).resolve()
OUTPUT=P(os.environ.get('POISE_CHECKPOINT_OUTPUT','/mnt/data/poise-checkpoints')).resolve()
BASE_MANIFEST=P(os.environ.get('POISE_BASE_MANIFEST',str(ROOT/'.cloud-recovery/BASE-RECOVERY-MANIFEST.json'))).resolve()
EVIDENCE_ROOTS=json.loads(os.environ.get('POISE_EVIDENCE_ROOTS','[]'))
BASE_SHA='d7890704cd40409cebeac0407d7ada39e0da5ce10ce8655e794731635be5be02'
BASE_HEAD='43e9bc2a5b17a2af4dcfc15554ec32a9c185f937'
KIT=P(__file__).resolve().parent

def sha(p):
    with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def git(repo,*args):return subprocess.check_output(['git','-C',str(repo),*args])
def sqlite_check(p):
    with contextlib.closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)],p
        assert not db.execute('PRAGMA foreign_key_check').fetchall(),p
        return db.execute('PRAGMA user_version').fetchone()[0]
def excluded(name):
    parts=P(name).parts
    return '.venv' in parts or '.python' in parts or '__pycache__' in parts or name.startswith('operator/state/backups/')
def build(name,task,note,artifact=None):
    if not name or P(name).name != name: raise ValueError('Checkpoint name must be a single basename')
    out=OUTPUT/name
    out.mkdir(parents=True,exist_ok=False)
    operator=ROOT/'poise-op'; repo=ROOT/'ai-poise'
    base=json.loads(BASE_MANIFEST.read_text())
    # No other processes or agents may write while this routine runs.
    r=subprocess.run(['bash',str(operator/'operator/poise.sh'),'backup','create','--config',str(operator/'operator/profile/project/project.json')],text=True,capture_output=True)
    (out/'backup-command.stdout.json').write_text(r.stdout);(out/'backup-command.stderr.txt').write_text(r.stderr)
    if r.returncode:raise RuntimeError('Public database backup failed')
    receipt=json.loads(r.stdout); assert receipt['success']
    dbcopy=operator/'operator/state/backups'/receipt['name']
    dbversion=sqlite_check(dbcopy)
    manifest={'schema':'poise-cloud-continuation/v1','base_archive_sha256':BASE_SHA,'base_commit':BASE_HEAD,'task':task,'note':note,'main_commit':git(repo,'rev-parse','HEAD').decode().strip(),'main_branch':git(repo,'branch','--show-current').decode().strip(),'database_version':dbversion,'database_sha256':sha(dbcopy),'operator_files':{},'operator_removed':[],'worktrees':[],'delivery_verified':False}
    refs=[manifest['main_branch']]
    if task:refs.append('tasks/'+task)
    git(repo,'bundle','create',str(out/'delta.bundle'),*refs,'^'+BASE_HEAD)
    (out/'bundle-verify.txt').write_bytes(git(repo,'bundle','verify',str(out/'delta.bundle')))
    for role,where in [('main',repo)]+([('task',repo/'projects/ai-poise/worktrees'/task)] if task else []):
        row={'role':role,'relative_path':str(where.relative_to(ROOT)),'head':git(where,'rev-parse','HEAD').decode().strip(),'branch':git(where,'branch','--show-current').decode().strip(),'status':git(where,'status','--porcelain=v1','--untracked-files=all').decode()}
        (out/(role+'-index.patch')).write_bytes(git(where,'diff','--cached','--binary'))
        (out/(role+'-work.patch')).write_bytes(git(where,'diff','--binary'))
        paths=git(where,'ls-files','--others','--exclude-standard','-z').decode().split('\0')
        row['untracked']=[]
        for rel in filter(None,paths):
            src=where/rel
            if src.is_symlink() or not src.is_file():raise RuntimeError(f'Untracked nonregular file: {src}')
            dest=out/'untracked'/role/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
            row['untracked'].append(rel)
        manifest['worktrees'].append(row)
    # Compare the complete non-reproducible operator tree with its preserved base.
    prefix='.cloud-handoff/poise-op/'
    basefiles={k[len(prefix):]:v for k,v in base['files'].items() if k.startswith(prefix) and not excluded(k[len(prefix):])}
    current={}
    for root,dirs,files in os.walk(operator):
        dirs[:]=[d for d in dirs if not excluded(str((P(root)/d).relative_to(operator))+'/')]
        for filename in files:
            p=P(root)/filename;rel=str(p.relative_to(operator))
            if excluded(rel):continue
            if p.is_symlink():raise RuntimeError(f'Unexpected operator symlink: {p}')
            source=dbcopy if rel=='operator/state/state.sqlite' else p
            item={'sha256':sha(source),'bytes':source.stat().st_size,'mode':p.stat().st_mode&0o777}
            current[rel]=item
            if item!=basefiles.get(rel):
                target=out/'operator'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);target.chmod(item['mode'])
                manifest['operator_files'][rel]=item
    manifest['operator_removed']=[rel for rel in basefiles if rel not in current]
    for p in KIT.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts:
            target=out/'kit'/p.relative_to(KIT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    for index, evidence in enumerate(EVIDENCE_ROOTS):
        evidence=P(evidence).resolve()
        if not evidence.is_dir(): raise ValueError(f'Missing evidence root: {evidence}')
        shutil.copytree(evidence,out/'evidence'/f'input-{index}',ignore=shutil.ignore_patterns('__pycache__'))
    # Standalone artifact for direct user inspection.
    manifest['artifact_relative_path']=artifact
    manifest['contract_sha256']=None
    if artifact is not None:
        if not task: raise ValueError('A Task artifact requires a Task worktree')
        from recovery.restore_continuation import relative_name, verify_task_artifact
        relative=relative_name(artifact)
        if '.git' in relative.parts: raise ValueError('Git metadata is not a Task artifact')
        path=repo/'projects/ai-poise/worktrees'/task/str(relative)
        if not path.is_file() or path.is_symlink(): raise ValueError('Declared artifact must exist')
        manifest['contract_sha256']=sha(path)
        verify_task_artifact(repo/'projects/ai-poise/worktrees'/task,manifest)
        shutil.copy2(path,out/path.name)
    from datetime import datetime, timezone
    manifest['prepared_at_utc']=datetime.now(timezone.utc).isoformat()
    manifest['backup_policy']={'min_seconds':600,'max_seconds':900}
    write(out/'CHECKPOINT.json',manifest)
    inventory={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}
    write(out/'FILES.json',inventory)
    (out/'SHA256SUMS').write_text(''.join(f'{h}  {rel}\n' for rel,h in inventory.items()))
    archive=out.with_suffix('.tar.gz')
    with tarfile.open(archive,'w:gz',compresslevel=6) as t:t.add(out,arcname=name)
    # Verify each archived payload from compressed bytes, not the source directory.
    with tarfile.open(archive,'r:gz') as t:
        for rel,h in inventory.items():
            with t.extractfile(name+'/'+rel) as f:assert hashlib.file_digest(f,'sha256').hexdigest()==h,rel
    archive_sha=sha(archive)
    archive.with_suffix(archive.suffix+'.sha256').write_text(f'{archive_sha}  {archive.name}\n')
    record={'prepared_at_utc':manifest['prepared_at_utc'],'archive':str(archive),'bytes':archive.stat().st_size,'sha256':archive_sha,'changed_operator_files':len(manifest['operator_files']),'database_sha256':manifest['database_sha256'],'main_commit':manifest['main_commit'],'task_commit':manifest['worktrees'][-1]['head'] if task else None,'contract_sha256':manifest.get('contract_sha256'),'artifact_relative_path':artifact,'task':task,'note':note}
    write(out.parent/(name+'-receipt.json'),record)
    print(json.dumps(record,indent=2))
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('name');a.add_argument('--task');a.add_argument('--note',required=True);a.add_argument('--artifact');v=a.parse_args();build(v.name,v.task,v.note,v.artifact)
