"""Same-format snapshot records only, not a public SQL or arbitrary table editor."""
from copy import deepcopy
import json
from ...common import PoiseError, encoded
from ...modules.transfers.placement import relocate_path

TASK_TABLES = ('tasks','submissions','section_layers','trace_point_layers','workflow_layers',
               'task_results','task_events','task_methods','content_contracts','task_workflows',
               'task_proofs','task_proof_layers','task_execution','evidence','action_runs','action_events',
               'handoffs','work_packets','interaction_reports')
SPRINT_TABLES = ('sprints','sprint_layers','sprint_members','sprint_dependencies','sprint_requests')
SHARED_TABLES = ('artifacts','task_artifacts','sprint_artifacts','interaction_events','interaction_bindings',
                 'journal','transfer_locations')
ACCOUNTING_TABLES = ('accounting_accounts','accounting_usage','accounting_cycles','accounting_credits','accounting_findings')
ALL_TABLES = TASK_TABLES + SPRINT_TABLES + SHARED_TABLES + ACCOUNTING_TABLES
SEQ_TABLES = {'task_events','action_events','handoffs','journal'}
SUBMISSION_TABLES = {'section_layers','trace_point_layers','workflow_layers','task_results'}


def insert(db, table, row):
    if table not in ALL_TABLES:raise PoiseError('Unknown snapshot table')
    columns=tuple(row)
    known={r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
    if not set(columns)<=known:raise PoiseError('Snapshot contains unknown columns')
    return db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                      [row[k] for k in columns])


def restore_tasks(db, tables, binding):
    """Called by the Task repository. Preserve content; rebind only execution metadata."""
    submap={}
    for old in tables['submissions']:
        row=deepcopy(old);sid=row.pop('seq')
        submap[sid]=insert(db,'submissions',row).lastrowid
    for table in TASK_TABLES:
        if table=='submissions':continue
        for old in tables[table]:
            row=deepcopy(old)
            if table in SEQ_TABLES:row.pop('seq')
            if table in SUBMISSION_TABLES:row['submission_id']=submap[row['submission_id']]
            if table=='tasks':
                row['current_submission_id']=None if row['current_submission_id'] is None else submap[row['current_submission_id']]
                meta=json.loads(row['metadata']);meta['config_hash']=binding['config_hash'];row['metadata']=encoded(meta)
            if table=='task_execution':
                value=json.loads(row['data'])
                value['worktree']=binding['worktrees'][row['task_id']]
                # Captured reports remain historical; location resolution is a read adapter.
                row['data']=encoded(value)
            if table=='handoffs':
                value=json.loads(row['data'])
                row['actor']=binding['namespace']+row['actor']
                value['actor']=row['actor'];value['receipt']=relocate_receipt(value['receipt'],binding['locations'])
                if value['plan'] is not None:
                    value['plan']=relocate_receipt(value['plan'],binding['locations'])
                row['data']=encoded(value)
            if table=='task_events':
                value=json.loads(row['data'])
                if value.get('submission') is not None:value['submission']=submap[value['submission']]
                row['data']=encoded(value)
            insert(db,table,row)
    return submap


def restore_sprints(db, tables, binding):
    for table in SPRINT_TABLES:
        for old in tables[table]:
            row=deepcopy(old)
            if table in ('sprints','sprint_layers'):
                value=json.loads(row['data']);value['execution_hash']=binding['config_hash'];row['data']=encoded(value)
            insert(db,table,row)


def relocate_receipt(value,locations):
    """Only mechanical path fields. Never rewrite section text, argv or an argument."""
    if value is None:return None
    if isinstance(value,list):return [relocate_receipt(x,locations) for x in value]
    if not isinstance(value,dict):return value
    result={}
    for key,item in value.items():
        if key in ('path','worktree','directory','manifest','stdout','stderr','bundle_path','receipt_path','captured_path'):
            result[key]=relocate_path(item,locations)
        elif key in ('artifact_paths','preserved_artifacts'):
            result[key]=[relocate_path(x,locations) for x in item]
        elif key=='artifact_mapping':
            result[key]={relocate_path(k,locations):relocate_path(v,locations) for k,v in item.items()}
        elif key in ('sections','content','text','argv','environment','facts','assumptions','argument','conclusion'):
            result[key]=item
        else:result[key]=relocate_receipt(item,locations)
    return result
