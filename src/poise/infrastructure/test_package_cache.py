from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import uuid
import xml.etree.ElementTree as ET

from ..common import PoiseError
from ..execution import RegisteredCheckRunner
from .test_packages import AiPoiseTestPackages


_CACHE_SCHEMA = 'ai-poise-test-package-cache-1'
_MEMBER_KINDS = ('source', 'tests', 'support', 'fixtures')


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def _relative_cache_path(value: object) -> Path:
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise PoiseError('AI-poise test cache contains an invalid relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts:
        raise PoiseError('AI-poise test cache path escapes the checkout')
    return Path(*path.parts)


def _terminal_junit(path: Path) -> tuple[list[dict], str | None]:
    if not path.is_file():
        return [], 'missing terminal JUnit report'
    try:
        document = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        return [], f'invalid terminal JUnit report: {exc}'
    rows=[]
    for case in document.getroot().iter('testcase'):
        if 'name' not in case.attrib:
            return rows, 'invalid testcase: missing name'
        outcome='passed'
        for tag in ('error','failure','skipped'):
            if case.find(tag) is not None:
                outcome=tag
                break
        rows.append({'name':case.attrib['name'],'file':case.attrib.get('file'),'outcome':outcome})
    if not rows:
        return [], 'empty terminal JUnit report'
    return rows, None


class AiPoiseTestPackageCache:
    """Content cache owned by the AI-poise codebase under test.

    Cache storage and every recorded evidence path are relative to the supplied
    AI-poise checkout.  No Poise state directory or external repository cache
    adapter participates in this mechanism.
    """

    def __init__(
        self,
        checkout: Path,
        catalog: Path,
        runner: RegisteredCheckRunner | None = None,
    ):
        self.checkout=checkout.resolve()
        self.packages=AiPoiseTestPackages.load(catalog)
        # Membership validation also establishes that this is an AI-poise checkout.
        if self.packages.package_ids:
            self.packages.membership(self.checkout,self.packages.package_ids[0])
        self.runner=RegisteredCheckRunner() if runner is None else runner
        self.cache=self.checkout/'.poise-test-cache'

    def fingerprint(self, package_id: str) -> dict:
        membership=self.packages.membership(self.checkout,package_id)
        content={}
        for kind in _MEMBER_KINDS:
            content[kind]=[
                {'path':relative,'sha256':_sha256(self.checkout/relative)}
                for relative in membership['members'][kind]
            ]
        identity={'membership':membership['members'],'content':content}
        return {
            'package_id':package_id,
            'input_fingerprint':_canonical_digest(identity),
            'membership':membership['members'],
            'content':content,
        }

    def _record_path(self, package_id: str, fingerprint: str) -> Path:
        return self.cache/'records'/package_id/f'{fingerprint}.json'

    def _relative(self,path: Path) -> str:
        resolved=path.resolve()
        if not resolved.is_relative_to(self.checkout):
            raise PoiseError('AI-poise test evidence must stay inside the checkout cache')
        return resolved.relative_to(self.checkout).as_posix()

    def _load_hit(self, package_id: str, fingerprint: str) -> dict | None:
        record_path=self._record_path(package_id,fingerprint)
        if not record_path.exists():
            return None
        if record_path.is_symlink() or not record_path.is_file() or not record_path.resolve().is_relative_to(self.checkout):
            raise PoiseError('AI-poise test cache record path is not a regular checkout-local file')
        try:
            record=json.loads(record_path.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError) as exc:
            raise PoiseError(f'Invalid AI-poise test cache record: {exc}') from exc
        if (not isinstance(record,dict) or record.get('schema')!=_CACHE_SCHEMA
                or record.get('package_id')!=package_id
                or record.get('input_fingerprint')!=fingerprint
                or not isinstance(record.get('origin_run_id'),str)):
            raise PoiseError('AI-poise test cache record provenance mismatch')
        evidence_rel=_relative_cache_path(record.get('evidence_path'))
        evidence_path=(self.checkout/evidence_rel).resolve()
        if (not evidence_path.is_relative_to(self.checkout) or evidence_path.is_symlink()
                or not evidence_path.is_file() or _sha256(evidence_path)!=record.get('evidence_sha256')):
            raise PoiseError('AI-poise cached test evidence is missing or changed')
        evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
        if (evidence.get('run_id')!=record['origin_run_id']
                or evidence.get('package_id')!=package_id
                or evidence.get('input_fingerprint')!=fingerprint
                or evidence.get('passed') is not True):
            raise PoiseError('AI-poise cached test evidence does not match its record')
        return {
            **evidence,
            'reused':True,
            'executed':False,
            'origin_run_id':record['origin_run_id'],
            'cache_record':self._relative(record_path),
        }

    def run(self, package_id: str, *, timeout_seconds: float, fresh: bool=False) -> dict:
        fingerprint=self.fingerprint(package_id)
        if not fresh:
            hit=self._load_hit(package_id,fingerprint['input_fingerprint'])
            if hit is not None:
                return hit
        tests=fingerprint['membership']['tests']
        if not tests:
            raise PoiseError(f'AI-poise test package {package_id} has no tests')
        run_id=str(uuid.uuid4())
        run_dir=self.cache/'evidence'/run_id
        run_dir.mkdir(parents=True,exist_ok=False)
        junit=run_dir/'junit.xml'
        stdout=run_dir/'stdout.log'
        stderr=run_dir/'stderr.log'
        command=[
            sys.executable,'-m','pytest',*tests,'-q','--tb=short',
            '-p','no:cacheprovider','--basetemp',str(run_dir/'pytest'),
            '--junitxml',str(junit),'-o','junit_family=legacy',
        ]
        result=self.runner.run(
            run_id,command,self.checkout,
            {**os.environ,'PYTHONPATH':str(self.checkout/'src')},
            timeout_seconds,stdout,stderr,
        )
        cases,junit_error=_terminal_junit(junit)
        passed=(
            result.get('capture_complete', True) is True
            and result['actual_exit_code']==0
            and not result['timed_out']
            and not result['cancelled']
            and junit_error is None
            and all(case['outcome'] in ('passed','skipped') for case in cases)
        )
        evidence={
            'schema':_CACHE_SCHEMA,
            'run_id':run_id,
            'origin_run_id':run_id,
            'package_id':package_id,
            'input_fingerprint':fingerprint['input_fingerprint'],
            'membership':fingerprint['membership'],
            'command':command,
            'actual_exit_code':result['actual_exit_code'],
            'timed_out':result['timed_out'],
            'cancelled':result['cancelled'],
            **{key: result[key] for key in ('capture_complete', 'capture_reason', 'cleanup_seconds') if key in result},
            'duration_seconds':result['duration_seconds'],
            'stdout':self._relative(stdout),
            'stderr':self._relative(stderr),
            'junit':self._relative(junit) if junit.exists() else None,
            'junit_error':junit_error,
            'cases':cases,
            'passed':passed,
            'reused':False,
            'executed':True,
        }
        evidence_path=run_dir/'result.json'
        evidence_path.write_text(json.dumps(evidence,sort_keys=True,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if passed:
            record_path=self._record_path(package_id,fingerprint['input_fingerprint'])
            record_path.parent.mkdir(parents=True,exist_ok=True)
            record={
                'schema':_CACHE_SCHEMA,
                'package_id':package_id,
                'input_fingerprint':fingerprint['input_fingerprint'],
                'origin_run_id':run_id,
                'evidence_path':self._relative(evidence_path),
                'evidence_sha256':_sha256(evidence_path),
            }
            record_path.write_text(json.dumps(record,sort_keys=True,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            evidence['cache_record']=self._relative(record_path)
        else:
            evidence['cache_record']=None
        return evidence
