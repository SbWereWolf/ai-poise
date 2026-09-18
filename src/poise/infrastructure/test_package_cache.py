from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
import sys
import uuid
import xml.etree.ElementTree as ET

from ..common import PoiseError
from ..execution import RegisteredCheckRunner
from .test_packages import AiPoiseTestPackages


_CACHE_SCHEMA = 'ai-poise-test-package-cache-2'
_PRIMARY_SCHEMA = 'ai-poise-primary-evidence-1'
_PRIMARY_FILES = {'stdout': 'stdout.log', 'stderr': 'stderr.log', 'junit': 'junit.xml'}
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
    if path.is_absolute() or '..' in path.parts or path.as_posix() != value:
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

    def _checked_file(self, relative: str, *, missing_ok: bool = False) -> Path | None:
        """Validate before resolve: even checkout-local symlink aliases are invalid."""
        parts = _relative_cache_path(relative).parts
        if not parts or parts[0] != '.poise-test-cache':
            raise PoiseError(f'Not a checkout cache path: {relative}')
        path = self.checkout
        for index, part in enumerate(parts):
            path = path / part
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                if missing_ok:
                    return None
                raise PoiseError(f'Missing cached evidence: {relative}') from None
            final = index == len(parts) - 1
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) if final else stat.S_ISDIR(mode)):
                raise PoiseError(f'Not a regular cache file/parent: {path}')
        return path

    def _primary_file(self, relative: str) -> dict:
        path = self._checked_file(relative)
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise PoiseError(f'Not regular cached evidence: {relative}')
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(stream.fileno())
        current = self._checked_file(relative).stat()
        def identity(value):
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns
        if identity(before) != identity(after) or identity(after) != identity(current) or size != after.st_size:
            raise PoiseError(f'Cached evidence changed during read: {relative}')
        return {'path': relative, 'bytes': size, 'sha256': digest.hexdigest()}

    def _load_hit(self, package_id: str, fingerprint: str) -> dict | None:
        # Corruption is not a miss. Only an absent record permits ordinary execution.
        try:
            return self._validated_hit(package_id, fingerprint)
        except (OSError, ValueError, TypeError, KeyError, PoiseError) as exc:
            raise PoiseError(f'Cannot reuse AI-poise cached evidence: {exc}; use --fresh for an explicit new run') from exc

    def _validated_hit(self, package_id: str, fingerprint: str) -> dict | None:
        record_relative = self._record_path(package_id, fingerprint).relative_to(self.checkout).as_posix()
        record_path = self._checked_file(record_relative, missing_ok=True)
        if record_path is None:
            return None
        record = json.loads(record_path.read_text(encoding='utf-8'))
        if (not isinstance(record, dict) or record.get('schema') != _CACHE_SCHEMA
                or record.get('package_id') != package_id
                or record.get('input_fingerprint') != fingerprint
                or not isinstance(record.get('origin_run_id'), str)):
            raise PoiseError(f'Legacy or mismatched cache record: {record_relative}')
        origin = record['origin_run_id']
        if str(uuid.UUID(origin)) != origin:
            raise PoiseError(f'Invalid cache origin: {record_relative}')
        run_relative = f'.poise-test-cache/evidence/{origin}'
        evidence_relative = f'{run_relative}/result.json'
        if record.get('evidence_path') != evidence_relative:
            raise PoiseError(f'Cache record points outside its own run: {record_relative}')
        evidence_path = self._checked_file(evidence_relative)
        payload = evidence_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != record.get('evidence_sha256'):
            raise PoiseError(f'Cached evidence is changed: {evidence_relative}')
        evidence = json.loads(payload)
        if (not isinstance(evidence, dict) or evidence.get('schema') != _CACHE_SCHEMA
                or evidence.get('run_id') != origin or evidence.get('origin_run_id') != origin
                or evidence.get('package_id') != package_id
                or evidence.get('input_fingerprint') != fingerprint
                or evidence.get('passed') is not True
                or evidence.get('capture_complete') is not True
                or evidence.get('actual_exit_code') != 0
                or evidence.get('timed_out') is not False or evidence.get('cancelled') is not False):
            raise PoiseError(f'Cached result does not match a complete successful origin: {evidence_relative}')
        manifest = evidence.get('primary_evidence')
        if (not isinstance(manifest, dict) or manifest.get('schema') != _PRIMARY_SCHEMA
                or not isinstance(manifest.get('files'), dict)
                or set(manifest['files']) != set(_PRIMARY_FILES)):
            raise PoiseError(f'Missing or invalid primary evidence manifest: {evidence_relative}')
        for role, name in _PRIMARY_FILES.items():
            relative = f'{run_relative}/{name}'
            row = manifest['files'][role]
            if (not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'}
                    or row['path'] != relative or evidence.get(role) != relative
                    or type(row['bytes']) is not int or row['bytes'] < 0
                    or row != self._primary_file(relative)):
                raise PoiseError(f'Missing or changed primary evidence: {relative}')
        cases, error = _terminal_junit(self.checkout / evidence['junit'])
        if error or cases != evidence.get('cases') or any(case['outcome'] not in ('passed', 'skipped') for case in cases):
            raise PoiseError(f'Invalid successful terminal JUnit: {evidence["junit"]}')
        return {
            **evidence,
            'reused': True,
            'executed': False,
            'origin_run_id': origin,
            'cache_record': record_relative,
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
        # A fresh run bypasses old contents, never path safety or cache ownership.
        self._checked_file(self._record_path(package_id, fingerprint['input_fingerprint']).relative_to(self.checkout).as_posix(), missing_ok=True)
        self._checked_file((run_dir / 'result.json').relative_to(self.checkout).as_posix(), missing_ok=True)
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
        primary_evidence = None
        evidence_error = None
        if passed:
            try:
                primary_evidence = {'schema': _PRIMARY_SCHEMA, 'files': {
                    role: self._primary_file(f'.poise-test-cache/evidence/{run_id}/{name}')
                    for role, name in _PRIMARY_FILES.items()
                }}
            except (OSError, PoiseError) as exc:
                # Keep diagnostics, but never publish an incomplete successful record.
                passed = False
                evidence_error = str(exc)
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
            'capture_complete':result.get('capture_complete', True),
            'primary_evidence':primary_evidence,
            'evidence_error':evidence_error,
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
