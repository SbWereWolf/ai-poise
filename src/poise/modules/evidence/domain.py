"""Evidence is a record, not truth. Command observations and reviews stay separate.

No environment, filesystem, database or model calls live in this module.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from ..foundation.errors import DomainError


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def identity(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def exact(value, fields, where):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise DomainError(f'{where}: требуется точный набор полей {sorted(fields)}')


def text(value, where):
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f'{where}: требуется непустой текст')


def strings(value, where):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise DomainError(f'{where}: требуется список непустых строк')


def completed_receipts(receipts):
    """Whether persisted command receipts have complete, usable control metadata."""
    required = {
        'actual_exit_code',
        'guard',
        'id',
        'interpretable',
        'method',
        'obligations',
        'passed',
        'timed_out',
        'tree',
    }
    if not isinstance(receipts, list):
        return False
    for receipt in receipts:
        if not isinstance(receipt, dict) or not required <= set(receipt):
            return False
        if any(
            not isinstance(receipt[field], str) or not receipt[field].strip()
            for field in ('id', 'method', 'tree')
        ):
            return False
        if (
            not isinstance(receipt['obligations'], list)
            or any(
                not isinstance(obligation, str) or not obligation.strip()
                for obligation in receipt['obligations']
            )
        ):
            return False
        if any(
            type(receipt[field]) is not bool
            for field in ('guard', 'interpretable', 'passed', 'timed_out')
        ):
            return False
        if (
            receipt['timed_out'] is not False
            or type(receipt['actual_exit_code']) is not int
            or receipt['actual_exit_code'] < 0
        ):
            return False
    return True


@dataclass(frozen=True)
class ArgumentRequirement:
    id: str
    kind: str
    phase: str
    observation_methods: tuple[str, ...]


@dataclass(frozen=True)
class StageEvidence:
    stage: str
    handler: str
    subject_methods: tuple[str, ...]
    observation_rules: tuple[tuple[str, str], ...]
    arguments: tuple[ArgumentRequirement, ...]
    review_arguments: tuple[str, ...]
    checks: tuple[str, ...]

    @property
    def active(self):
        return bool(self.subject_methods or self.arguments or self.review_arguments)


@dataclass(frozen=True)
class EvidencePlan:
    stages: tuple[StageEvidence, ...]

    @classmethod
    def parse(cls, raw: dict, handlers: dict, checks: dict):
        exact(raw, handlers, 'evidence_plan: каждый этап обязателен')
        exact(checks, handlers, 'checks')
        all_methods = {m for values in checks.values() for m in values}
        result, known_arguments = [], {}
        for stage, kind in handlers.items():
            cfg = raw[stage]
            exact(cfg, {'subject_methods','arguments','review_arguments'}, f'evidence_plan.{stage}')
            for field in ('review_arguments',):
                strings(cfg[field], field)
                if len(set(cfg[field])) != len(cfg[field]):
                    raise DomainError(f'Повтор {field}')
            if not isinstance(cfg['subject_methods'],dict):
                raise DomainError('subject_methods: явный объект method ID → observation rule')
            for mid, rule in cfg['subject_methods'].items():
                text(mid,'subject method')
                exact(rule, {'exit_codes','stdout_contains','stderr_contains'}, 'observation rule')
                if (not isinstance(rule['exit_codes'],list) or not rule['exit_codes'] or
                        any(type(c) is not int or c < 0 for c in rule['exit_codes']) or
                        len(set(rule['exit_codes']))!=len(rule['exit_codes'])):
                    raise DomainError('Для наблюдения нужны явные допустимые exit codes')
                strings(rule['stdout_contains'],'observation stdout_contains')
                strings(rule['stderr_contains'],'observation stderr_contains')
            if set(cfg['subject_methods']) - set(checks[stage]):
                raise DomainError('Subject method должен иметь точную команду в checks текущего этапа')
            if cfg['subject_methods'] and kind not in ('observe','check'):
                raise DomainError('Subject methods разрешены только observe/check')
            if cfg['review_arguments'] and kind != 'inspect':
                raise DomainError('Решение о доказательстве принимает отдельный inspect')
            if not isinstance(cfg['arguments'], list):
                raise DomainError('arguments должен быть списком')
            args = []
            for a in cfg['arguments']:
                exact(a, {'id','kind','phase','observation_methods'}, 'argument requirement')
                text(a['id'], 'argument id')
                if a['id'] in known_arguments:
                    raise DomainError('ID аргумента объявляется в одном этапе')
                if a['kind'] not in ('logical','inspection') or a['phase'] not in ('prepare','continue'):
                    raise DomainError('Неизвестный вид/фаза доказательства')
                strings(a['observation_methods'], 'observation_methods')
                if len(set(a['observation_methods'])) != len(a['observation_methods']):
                    raise DomainError('Повтор метода наблюдения')
                if set(a['observation_methods']) - all_methods:
                    raise DomainError('Неизвестный метод наблюдения')
                if a['phase'] == 'continue' and (not a['observation_methods'] or set(a['observation_methods'])-set(checks[stage])):
                    raise DomainError('CONTINUE требует явно запланированные наблюдения текущего этапа')
                if a['phase'] == 'prepare' and set(a['observation_methods']) & set(checks[stage]):
                    raise DomainError('PREPARE не может требовать будущие наблюдения этого этапа')
                if kind == 'inspect':
                    raise DomainError('Осмотр не создаёт собственный предмет доказательства')
                args.append(ArgumentRequirement(a['id'],a['kind'],a['phase'],tuple(a['observation_methods'])))
                known_arguments[a['id']] = stage
            if kind == 'check' and not cfg['subject_methods'] and not args:
                raise DomainError('check требует subject method или аргумент')
            if kind == 'observe' and not cfg['subject_methods']:
                raise DomainError('observe требует subject method')
            result.append(StageEvidence(stage,kind,tuple(cfg['subject_methods']),
                                        tuple((mid,canonical(rule)) for mid,rule in cfg['subject_methods'].items()),tuple(args),
                                        tuple(cfg['review_arguments']),tuple(checks[stage])))
        for s in result:
            if set(s.review_arguments) - set(known_arguments):
                raise DomainError('Осмотр ссылается на необъявленный аргумент')
            if any(known_arguments[a] == s.stage for a in s.review_arguments):
                raise DomainError('Нельзя принимать своё доказательство в том же этапе')
        return cls(tuple(result))

    def stage(self, stage):
        for s in self.stages:
            if s.stage == stage:
                return s
        raise DomainError(f'Неизвестный evidence stage {stage}')

    def validate_work(self, stage, work):
        exact(work, {'phase','arguments','decisions'}, 'evidence_work')
        if work['phase'] not in ('prepare','continue'):
            raise DomainError('evidence_work.phase: prepare/continue')
        if not isinstance(work['arguments'],list) or not isinstance(work['decisions'],list):
            raise DomainError('arguments/decisions: списки')
        cfg = self.stage(stage)
        expected = {a.id:a for a in cfg.arguments}
        seen = set()
        for a in work['arguments']:
            exact(a, {'id','kind','facts','assumptions','inference','conclusion','verdict','observation_ids'}, 'argument')
            if not isinstance(a['id'],str) or a['id'] not in expected or a['id'] in seen:
                raise DomainError('Неизвестный/повторный ID аргумента')
            seen.add(a['id']); spec = expected[a['id']]
            if a['kind'] != spec.kind or (work['phase']=='prepare' and spec.phase=='continue'):
                raise DomainError('Аргумент подан не в заданную фазу/не того вида')
            strings(a['facts'],'facts'); strings(a['assumptions'],'assumptions'); strings(a['observation_ids'],'observation_ids')
            if not a['facts'] or len(set(a['observation_ids'])) != len(a['observation_ids']):
                raise DomainError('Нужны факты и уникальные ссылки на наблюдения')
            text(a['inference'],'inference'); text(a['conclusion'],'conclusion')
            if a['verdict'] not in ('proved','disproved','inconclusive'):
                raise DomainError('verdict: proved/disproved/inconclusive')
        seen = set()
        for d in work['decisions']:
            exact(d, {'argument_id','revision','decision','reason'}, 'evidence decision')
            for field in d: text(d[field],field)
            if d['argument_id'] not in cfg.review_arguments or d['argument_id'] in seen:
                raise DomainError('Решение относится не к запланированному предмету осмотра')
            seen.add(d['argument_id'])
            if d['decision'] not in ('accepted','rejected'):
                raise DomainError('Решение осмотра: accepted/rejected')
        if work['phase']=='continue' and not any(a.phase=='continue' for a in cfg.arguments):
            raise DomainError('CONTINUE не объявлен для этого этапа')

    def describe(self, stage):
        s=self.stage(stage)
        return {'subject_methods':{mid:json.loads(rule) for mid,rule in s.observation_rules},
                'arguments':[{'id':a.id,'kind':a.kind,'phase':a.phase,'observation_methods':list(a.observation_methods)} for a in s.arguments],
                'review_arguments':list(s.review_arguments)}


@dataclass(frozen=True)
class EvidenceAssessment:
    ready: bool
    needs_continuation: bool
    outcome: str | None
    missing: tuple[str, ...]
    book: EvidenceBook

    def to_dict(self):
        return {'ready':self.ready,'needs_continuation':self.needs_continuation,
                'outcome':self.outcome,'missing':list(self.missing)}


@dataclass(frozen=True)
class EvidenceBook:
    # Canonical immutable JSON records; no mutable dicts escape stored state.
    batches: tuple[str, ...]
    arguments: tuple[str, ...]
    decisions: tuple[str, ...]

    @classmethod
    def empty(cls):
        return cls((), (), ())

    def to_dict(self):
        return {name:[json.loads(x) for x in getattr(self,name)] for name in ('batches','arguments','decisions')}

    @classmethod
    def from_dict(cls, raw):
        exact(raw, {'batches','arguments','decisions'}, 'evidence book')
        if any(not isinstance(raw[k],list) for k in raw):
            raise DomainError('Evidence book collections must be explicit lists')
        return cls(*(tuple(canonical(x) for x in raw[k]) for k in ('batches','arguments','decisions')))

    def record_batch(self, stage, iteration, tree, key, receipts):
        return self._record_batch(stage,iteration,tree,key,None,receipts)

    def record_submission_batch(self, stage, iteration, tree, key, submission_digest, receipts):
        text(submission_digest,'submission_digest')
        return self._record_batch(stage,iteration,tree,key,submission_digest,receipts)

    def _record_batch(self, stage, iteration, tree, key, submission_digest, receipts):
        text(stage,'stage'); text(tree,'tree'); text(key,'execution_key')
        if type(iteration) is not int or iteration < 1 or not isinstance(receipts,list):
            raise DomainError('Некорректная итерация/набор observations')
        ids=set()
        for r in receipts:
            if not isinstance(r,dict) or not {'id','method','obligations','passed','timed_out','actual_exit_code','tree','guard','interpretable'} <= set(r):
                raise DomainError('Неполный receipt команды')
            text(r['id'],'receipt id'); strings(r['obligations'],'obligations')
            if r['id'] in ids or r['tree'] != tree or type(r['passed']) is not bool or type(r['timed_out']) is not bool or type(r['guard']) is not bool or type(r['interpretable']) is not bool:
                raise DomainError('Receipt не соответствует наблюдаемому состоянию')
            if r['actual_exit_code'] is not None and type(r['actual_exit_code']) is not int:
                raise DomainError('Неверный actual_exit_code')
            ids.add(r['id'])
        identity_parts=[stage,iteration,tree,key,[r['id'] for r in receipts]]
        if submission_digest is not None:identity_parts.append(submission_digest)
        ident=identity(identity_parts)
        value={'id':ident,'stage':stage,'iteration':iteration,'tree':tree,'execution_key':key,'receipts':receipts}
        if submission_digest is not None:value['submission_digest']=submission_digest
        encoded=canonical(value)
        for old in self.batches:
            prior=json.loads(old)
            if prior['id']==ident:
                if old != encoded: raise DomainError('Нельзя подменить immutable observation batch')
                return self
            known={r['id']:r for r in prior['receipts']}
            if any(r['id'] in known and known[r['id']] != r for r in receipts):
                raise DomainError('Receipt identity conflict')
        return EvidenceBook(self.batches+(encoded,),self.arguments,self.decisions)

    def batch(self, stage, iteration, tree, key):
        for item in reversed(self.batches):
            b=json.loads(item)
            if (b['stage'],b['iteration'],b['tree'],b['execution_key']) == (stage,iteration,tree,key):
                return b
        return None

    def failed_batch(self, stage, iteration, submission_digest, tree, key):
        for item in reversed(self.batches):
            try:
                batch=json.loads(item)
            except (TypeError, json.JSONDecodeError):
                return None
            identity_fields={'id','stage','iteration','submission_digest','tree','execution_key','receipts'}
            if not isinstance(batch,dict) or not identity_fields <= set(batch):
                return None
            if (batch['stage'],batch['iteration'],batch.get('submission_digest'),batch['tree'],batch['execution_key']) != (
                    stage,iteration,submission_digest,tree,key):
                continue
            receipts=batch['receipts']
            return batch if receipts and completed_receipts(receipts) and any(
                (r['guard'] and not r['passed']) or not r['interpretable']
                for r in receipts
            ) else None
        return None

    def latest_argument(self, id):
        for item in reversed(self.arguments):
            a=json.loads(item)
            if a['id']==id: return a
        return None

    def _validate_sources(self, cfg, argument, tree, batch):
        spec=next(a for a in cfg.arguments if a.id==argument['id'])
        receipts={r['id']:(b,r) for encoded in self.batches for b in [json.loads(encoded)] for r in b['receipts']}
        methods=set()
        for rid in argument['observation_ids']:
            if rid not in receipts:
                raise DomainError('Аргумент ссылается на неизвестное/чужое наблюдение')
            b,r=receipts[rid]
            if b['tree'] != tree or not r['interpretable'] or r['timed_out'] or r['actual_exit_code'] is None or r['actual_exit_code'] < 0:
                raise DomainError('Аргумент ссылается на устаревшее/незавершённое наблюдение')
            if spec.phase=='continue' and (batch is None or b['id'] != batch['id']):
                raise DomainError('CONTINUE должен использовать текущий batch наблюдений')
            if spec.phase=='prepare' and b['stage']==cfg.stage:
                raise DomainError('PREPARE не использует будущие наблюдения текущего этапа')
            methods.update(r['obligations'])
        if set(spec.observation_methods)-methods:
            raise DomainError('Не все обязательные методы подтверждены ссылками на наблюдения')
        if not spec.observation_methods and argument['observation_ids']:
            raise DomainError('Наблюдения должны быть явно предусмотрены argument contract')

    def precheck(self, plan, stage, iteration, tree, key, work):
        plan.validate_work(stage,work)
        cfg=plan.stage(stage); batch=self.batch(stage,iteration,tree,key)
        if work['phase']=='continue' and batch is None:
            raise DomainError('Наблюдения CONTINUE отсутствуют/устарели')
        args={a['id']:a for a in work['arguments']}
        missing=[]
        for spec in cfg.arguments:
            if spec.phase=='prepare' and spec.id not in args:
                missing.append(f'argument:{spec.id}:prepare')
        for a in work['arguments']:
            self._validate_sources(cfg,a,tree,batch)
        decisions={d['argument_id']:d for d in work['decisions']}
        for id in cfg.review_arguments:
            if id not in decisions:
                missing.append(f'review:{id}')
                continue
            a=self.latest_argument(id); d=decisions[id]
            if a is None or a['revision'] != d['revision'] or a['tree'] != tree:
                raise DomainError('Осмотр требует точную актуальную revision аргумента')
        return tuple(missing)

    def assess(self, plan, stage, iteration, tree, key, work):
        missing=list(self.precheck(plan,stage,iteration,tree,key,work))
        cfg=plan.stage(stage); batch=self.batch(stage,iteration,tree,key)
        receipts=[] if batch is None else batch['receipts']
        covered={m for r in receipts for m in r['obligations']}
        missing.extend(f'command:{m}' for m in cfg.checks if m not in covered)
        execution_bad=False
        for r in receipts:
            transport_bad=not r['interpretable'] or r['timed_out'] or r['actual_exit_code'] is None or r['actual_exit_code'] < 0
            guard=r['guard']
            if transport_bad or (guard and not r['passed']):
                execution_bad=True; missing.append(f'command_unusable:{r["id"]}')
        args={a['id']:a for a in work['arguments']}
        continuation_missing=[a.id for a in cfg.arguments if a.phase=='continue' and a.id not in args]
        missing.extend(f'argument:{id}:continue' for id in continuation_missing)
        book=self
        # Do not certify/persist propositions using failed guards/transport or missing prerequisites.
        blocking=[x for x in missing if not x.endswith(':continue')]
        if not blocking:
            proposals=list(self.arguments)
            for a in work['arguments']:
                rev=identity([stage,iteration,tree,a])
                value=canonical({'id':a['id'],'revision':rev,'stage':stage,'iteration':iteration,'tree':tree,'body':a})
                latest=next((x for x in reversed(proposals) if json.loads(x)['id']==a['id']),None)
                if latest != value: proposals.append(value)
            decisions=list(self.decisions)
            for d in work['decisions']:
                value=canonical({**d,'stage':stage,'iteration':iteration,'tree':tree})
                latest=next((x for x in reversed(decisions) if (json.loads(x)['argument_id'],json.loads(x)['revision'])==(d['argument_id'],d['revision'])),None)
                if latest != value: decisions.append(value)
            book=EvidenceBook(self.batches,tuple(proposals),tuple(decisions))
        ready=not missing
        outcome=None
        if ready:
            if cfg.handler=='inspect':
                outcome='changes_requested' if any(d['decision']=='rejected' for d in work['decisions']) else 'clear'
            elif cfg.handler=='check':
                outcomes=[a['verdict'] for a in work['arguments']]
                if any(not r['passed'] for r in receipts if set(r['obligations']) & set(cfg.subject_methods)) or 'disproved' in outcomes:
                    outcome='not_satisfied'
                elif 'inconclusive' in outcomes:
                    outcome='inconclusive'
                else: outcome='satisfied'
            else: outcome='complete'
        return EvidenceAssessment(ready, bool(continuation_missing) and not blocking and not execution_bad,
                                  outcome,tuple(missing),book)
