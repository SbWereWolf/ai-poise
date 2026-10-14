"""Independent origin and byte checks for the actual consumer supply."""
import hashlib
import json
import os
from pathlib import Path
import zipfile


REPO = Path(__file__).resolve().parents[2]


class SupplyGap(AssertionError):
    pass


def test_supply_matches_exact_source_wheel_and_manifest():
    wheel = Path(os.environ["AIP_GENERIC_WHEEL"])
    build = json.loads(Path(os.environ["AIP_GENERIC_BUILD"]).read_text())
    bundle = Path(os.environ["AIP_GENERIC_SOURCE"])
    # Prerequisite failures remain ordinary assertion/input errors, never RED.
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == "42c113c71b04cee01acb9be78ff9749ee1af47b935beed0024f2dcfa2d7c7e28"
    assert build["source_commit"] == "a980fcd54d0958e0a491efb385ede406453d6899"
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert digest == build["wheel_sha256"]
    with zipfile.ZipFile(wheel) as package:
        metadata = package.read("environment_maintenance-0.2.0.dist-info/METADATA")
        assert b"Name: environment-maintenance\n" in metadata
        assert b"Version: 0.2.0\n" in metadata
    lock = json.loads((REPO / "apps/bootstrap/component.json").read_text())
    assert lock["schema"] == "ai-poise/bootstrap-component/v1"
    assert lock["distribution"] == "environment-maintenance"
    assert lock["version"] == "0.2.0"
    assert lock["accepted_commit"] == "a980fcd54d0958e0a491efb385ede406453d6899"
    if lock["wheel_sha256"] != digest:
        raise SupplyGap("SUPPLY_DIGEST_MISMATCH")
    manifest_path = Path(os.environ["AIP_GENERIC_DELIVERY"])
    if not manifest_path.is_file():
        raise SupplyGap("SUPPLY_MANIFEST_MISSING")
    raw = manifest_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == lock["delivery_manifest_sha256"]
    manifest = json.loads(raw)
    assert manifest["source_commit"] == build["source_commit"]
    assert manifest["source_bundle_sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert manifest["wheel"] == wheel.name
    assert manifest["wheel_sha256"] == digest
    assert manifest["version"] == "0.2.0"
    assert manifest["distribution"] == "environment-maintenance"
    assert manifest["requires_python"] == ">=3.11"
    assert manifest["build"] == build


if __name__ == "__main__":
    try:
        test_supply_matches_exact_source_wheel_and_manifest()
    except SupplyGap as gap:
        print(str(gap))
        raise SystemExit(1)
    print("SUPPLY_VERIFIED")
