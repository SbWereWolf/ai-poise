"""Focused restoration tests; no live operator or production DB is used."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from restore_continuation import RecoveryError, apply_worktree, extract_checkpoint


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RestoreBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, payload=None, extra=None, inventory=None):
        data = {'CHECKPOINT.json': b'{"schema":"poise-cloud-continuation/v1"}\n',
                'nested/file.txt': b'durable\n'} if payload is None else payload
        hashes = {n: hashlib.sha256(v).hexdigest() for n, v in data.items()}
        if inventory is not None:
            hashes = inventory
        data = {**data, 'FILES.json': json.dumps(hashes).encode(),
                'SHA256SUMS': ''.join(f'{h}  {n}\n' for n, h in hashes.items()).encode()}
        path = self.root / 'input.tar.gz'
        with tarfile.open(path, 'w:gz') as t:
            for n, v in data.items():
                info = tarfile.TarInfo('one-root/' + n)
                info.mode = 0o600
                info.size = len(v)
                t.addfile(info, io.BytesIO(v))
            for info, v in (extra or []):
                t.addfile(info, io.BytesIO(v) if v is not None else None)
        return path

    def test_valid_roundtrip_preserves_file_mode(self):
        p = self.archive()
        out = extract_checkpoint(p, self.root/'out', sha(p))
        self.assertEqual((out/'nested/file.txt').read_bytes(), b'durable\n')
        self.assertEqual((out/'nested/file.txt').stat().st_mode & 0o777, 0o600)

    def test_inherited_setgid_directory_is_safely_stripped(self):
        info = tarfile.TarInfo('one-root/inherited')
        info.type = tarfile.DIRTYPE; info.mode = 0o2755
        p = self.archive(extra=[(info, None)])
        out = extract_checkpoint(p, self.root/'out', sha(p))
        self.assertEqual((out/'inherited').stat().st_mode & 0o7777, 0o755)

    def test_wrong_hash_leaves_no_destination(self):
        p = self.archive()
        with self.assertRaises(RecoveryError):
            extract_checkpoint(p, self.root/'out', '0'*64)
        self.assertFalse((self.root/'out').exists())

    def test_existing_destination_is_preserved(self):
        p = self.archive()
        out = self.root/'out'; out.mkdir(); (out/'sentinel').write_text('keep')
        with self.assertRaises(RecoveryError):
            extract_checkpoint(p, out, sha(p))
        self.assertEqual((out/'sentinel').read_text(), 'keep')

    def test_traversal_rejected_before_publication(self):
        info = tarfile.TarInfo('one-root/../../escape'); info.size = 1
        p = self.archive(extra=[(info, b'x')])
        with self.assertRaises(RecoveryError):
            extract_checkpoint(p, self.root/'out', sha(p))
        self.assertFalse((self.root/'out').exists())
        self.assertFalse((self.root/'escape').exists())

    def test_symlink_rejected(self):
        info = tarfile.TarInfo('one-root/link'); info.type = tarfile.SYMTYPE
        info.linkname = '/tmp'
        p = self.archive(extra=[(info, None)])
        with self.assertRaises(RecoveryError):
            extract_checkpoint(p, self.root/'out', sha(p))
        self.assertFalse((self.root/'out').exists())

    def test_duplicate_rejected(self):
        info = tarfile.TarInfo('one-root/nested/file.txt'); info.size = 1
        p = self.archive(extra=[(info, b'x')])
        with self.assertRaises(RecoveryError):
            extract_checkpoint(p, self.root/'out', sha(p))
        self.assertFalse((self.root/'out').exists())

    def test_unlisted_payload_and_missing_payload_rejected(self):
        for inventory in ({}, {'missing': '0'*64}):
            with self.subTest(inventory=inventory):
                p = self.archive(inventory=inventory)
                with self.assertRaises(RecoveryError):
                    extract_checkpoint(p, self.root/'out', sha(p))
                self.assertFalse((self.root/'out').exists())

    def test_unpacked_limit_enforced(self):
        p = self.archive()
        with self.assertRaises(RecoveryError):
            extract_checkpoint(p, self.root/'out', sha(p), max_bytes=1)
        self.assertFalse((self.root/'out').exists())

    def test_index_unstaged_and_untracked_roundtrip(self):
        repo = self.root/'repo'; repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(repo), *args])
        git('init', '-q'); git('config','user.name','Restore test')
        git('config','user.email','restore-test@example.invalid')
        (repo/'tracked.txt').write_text('base\n'); git('add','tracked.txt')
        git('commit','-qm','fixture')
        head = git('rev-parse','HEAD').decode().strip()
        (repo/'tracked.txt').write_text('staged\n'); git('add','tracked.txt')
        (repo/'tracked.txt').write_text('work\n'); (repo/'untracked.txt').write_text('untracked\n')
        stage = self.root/'overlay'; stage.mkdir()
        (stage/'task-index.patch').write_bytes(git('diff','--cached','--binary'))
        (stage/'task-work.patch').write_bytes(git('diff','--binary'))
        target = stage/'untracked/task'; target.mkdir(parents=True)
        (target/'untracked.txt').write_text('untracked\n')
        expected = git('status','--porcelain=v1','--untracked-files=all').decode()
        row = {'role':'task','head':head,'branch':git('branch','--show-current').decode().strip(),
               'status':expected,'untracked':['untracked.txt']}
        git('reset','--hard','HEAD'); (repo/'untracked.txt').unlink()
        apply_worktree(repo, stage, row)
        self.assertEqual(git('show',':tracked.txt'), b'staged\n')
        self.assertEqual((repo/'tracked.txt').read_text(), 'work\n')
        self.assertEqual(git('status','--porcelain=v1','--untracked-files=all').decode(), expected)

if __name__ == '__main__':
    unittest.main(verbosity=2)
