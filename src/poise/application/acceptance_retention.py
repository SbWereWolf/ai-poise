"""Verification closure; callers retain their lifecycle and transaction owners."""
from copy import deepcopy

from ..common import digest, PoiseError
from ..modules.verification.retention import ArtifactInputs, AcceptanceManifest
from ..modules.verification.retention_ports import AcceptanceRetentionPort


class AcceptanceRetention:
    def __init__(self, port: AcceptanceRetentionPort):
        self.port = port

    def bind_inputs(self, task, method):
        if 'artifact_inputs' not in method:
            return {}
        declaration = ArtifactInputs.parse(method['artifact_inputs'], method['environment'])
        inputs = self.port.inputs(task, declaration)
        return {item['environment']: fact['path']
                for item, fact in zip(declaration.files, inputs, strict=True)}

    def _expected(self, task, method, receipt, commit, tree, definition_digest):
        declaration = ArtifactInputs.parse(method['artifact_inputs'], {})
        inputs = self.port.inputs(task, declaration)
        proof = self.port.evidence(task, method, receipt)
        return declaration, AcceptanceManifest.build(
            task['id'], method['id'], commit, tree, inputs, proof, receipt, definition_digest,
        )

    def create(self, task, method, receipt, commit, tree):
        if 'artifact_inputs' not in method:
            return
        snapshot = {key: deepcopy(method[key]) for key in ('id', 'artifact_inputs')}
        snapshot['outputs'] = deepcopy(method.get('outputs', []))
        definition_digest = digest(method)
        declaration, expected = self._expected(task, snapshot, receipt, commit, tree, definition_digest)
        reference = self.port.publish(task, declaration, receipt['id'], expected)
        # Read actual published producer data; a valid file digest is not complete proof.
        AcceptanceManifest.validate(self.port.read(task, reference), expected)
        receipt['acceptance_manifest'] = reference
        receipt['retention_method'] = snapshot
        receipt['retention_definition_digest'] = definition_digest
        receipt['retention_tree'] = tree

    def validate_receipt(self, task, receipt, commit, method=None):
        if method is not None and 'artifact_inputs' in method:
            snapshot = {key: deepcopy(method[key]) for key in ('id', 'artifact_inputs')}
            snapshot['outputs'] = deepcopy(method.get('outputs', []))
            if (receipt.get('retention_method') != snapshot
                    or receipt.get('retention_definition_digest') != digest(method)
                    or 'acceptance_manifest' not in receipt):
                raise PoiseError('Current candidate retained proof definition missing/mismatched')
        if 'retention_method' not in receipt:
            return
        _, expected = self._expected(
            task, receipt['retention_method'], receipt, commit, receipt['retention_tree'],
            receipt['retention_definition_digest'],
        )
        AcceptanceManifest.validate(self.port.read(task, receipt['acceptance_manifest']), expected)

    def validate_bundle(self, task, report, submitted_paths=()):
        if report is None:
            self.port.registered(task, list(submitted_paths))
            return
        for receipt in report['checks']:
            self.validate_receipt(task, receipt, report['commit'])
        self.port.registered(task, list(submitted_paths))
        expected = self.references(report['checks'])
        if expected and report.get('acceptance_manifests') != expected:
            raise PoiseError('Acceptance manifest proof references missing from report')

    @staticmethod
    def references(receipts):
        return [deepcopy(item['acceptance_manifest']) for item in receipts
                if 'acceptance_manifest' in item]
