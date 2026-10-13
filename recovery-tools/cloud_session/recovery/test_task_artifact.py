"""Task artifact verification across legacy and new cumulative checkpoints."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import restore_continuation as recovery


class TaskArtifactTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = 'apps/execution/docs/contract-exe.md'
        target = self.root / self.path
        target.parent.mkdir(parents=True)
        target.write_bytes(b'Execution contract\n')
        self.sha = hashlib.sha256(target.read_bytes()).hexdigest()

    def check(self, **values):
        return recovery.verify_task_artifact(self.root, values)

    def test_execution_artifact_is_verified(self):
        self.assertEqual(self.check(artifact_relative_path=self.path, contract_sha256=self.sha), self.sha)

    def test_legacy_sequencing_artifact_is_verified(self):
        old = self.root / 'apps/sequencing/docs/contract-seq.md'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'Execution contract\n')
        self.assertEqual(self.check(contract_sha256=self.sha), self.sha)

    def test_explicit_absence_before_design_is_allowed(self):
        self.assertIsNone(self.check(artifact_relative_path=None, contract_sha256=None))

    def test_absence_cannot_hide_declared_digest(self):
        with self.assertRaises(recovery.RecoveryError):
            self.check(artifact_relative_path=None, contract_sha256=self.sha)

    def test_declared_missing_artifact_is_rejected(self):
        with self.assertRaises(recovery.RecoveryError):
            self.check(artifact_relative_path='missing.md', contract_sha256=self.sha)

    def test_incorrect_artifact_digest_is_rejected(self):
        with self.assertRaises(recovery.RecoveryError):
            self.check(artifact_relative_path=self.path, contract_sha256='0'*64)

    def test_traversal_and_absolute_path_are_rejected(self):
        for path in ('../outside', '/outside', '.git/config'):
            with self.subTest(path=path), self.assertRaises(recovery.RecoveryError):
                self.check(artifact_relative_path=path, contract_sha256=self.sha)

    def test_symlink_is_rejected(self):
        (self.root/'alias.md').symlink_to(self.root/self.path)
        with self.assertRaises(recovery.RecoveryError):
            self.check(artifact_relative_path='alias.md', contract_sha256=self.sha)


if __name__ == '__main__':
    unittest.main(verbosity=2)
