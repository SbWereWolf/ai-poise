"""Independent origin and byte checks for the actual consumer supply."""
import hashlib
import json
import os
from pathlib import Path
import zipfile


REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")


class SupplyGap(AssertionError):
    pass


def independent_unique_pairs(pairs):
    assert len(pairs) == len({key for key, _ in pairs}), "Duplicate lock field"
    return dict(pairs)


def test_supply_matches_exact_source_wheel_and_manifest():
    wheel = Path(os.environ["AIP_GENERIC_WHEEL"])
    build = json.loads(Path(os.environ["AIP_GENERIC_BUILD"]).read_text())
    bundle = Path(os.environ["AIP_GENERIC_SOURCE"])
    # Prerequisite failures remain ordinary assertion/input errors, never RED.
    inputs = json.loads((FIXTURES / "v2_supply_inputs.json").read_text())
    for item in inputs["files"]:
        source = Path(os.environ[item["environment"]])
        assert source.name == item["name"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"]
    assert build["source_commit"] == "482b11417f8e74d9bcc1d885767bb16837f3284a"
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert digest == build["wheel_sha256"]
    with zipfile.ZipFile(wheel) as package:
        metadata = package.read("environment_maintenance-0.2.1.dist-info/METADATA")
        assert b"Name: environment-maintenance\n" in metadata
        assert b"Version: 0.2.1\n" in metadata
        assert b"Requires-Python: >=3.11\n" in metadata
        assert not any(name.startswith("poise/") for name in package.namelist())
    manifest_path = Path(os.environ["AIP_GENERIC_DELIVERY"])
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    assert manifest["source_commit"] == build["source_commit"]
    assert manifest["source_bundle_sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert manifest["wheel"] == wheel.name
    assert manifest["wheel_sha256"] == digest
    assert manifest["version"] == "0.2.1"
    assert manifest["distribution"] == "environment-maintenance"
    assert manifest["requires_python"] == ">=3.11"
    assert manifest["build"] == build
    assert build["exit_code"] == 0
    assert hashlib.sha256(Path(os.environ["AIP_GENERIC_BUILD_STDOUT"]).read_bytes()).hexdigest() == build["stdout_sha256"]
    assert hashlib.sha256(Path(os.environ["AIP_GENERIC_BUILD_STDERR"]).read_bytes()).hexdigest() == build["stderr_sha256"]
    lock = json.loads((REPO / "apps/bootstrap/component.json").read_bytes().decode("utf-8"),
                      object_pairs_hook=independent_unique_pairs)
    selected = json.loads((FIXTURES / "v2_selected_component.json").read_text())
    assert type(lock) is dict
    assert set(lock) == set(selected)
    for value in lock.values():
        assert isinstance(value, str) and value.strip() and "\x00" not in value
        value.encode("utf-8", errors="strict")
    assert lock["schema"] == "ai-poise/bootstrap-component/v1"
    assert lock["distribution"] == "environment-maintenance"
    # Valid accepted inputs plus a different real product selection is the RED.
    if lock != selected:
        raise SupplyGap("SUPPLY_SELECTION_MISMATCH")
    assert hashlib.sha256(raw).hexdigest() == lock["delivery_manifest_sha256"]


if __name__ == "__main__":
    try:
        test_supply_matches_exact_source_wheel_and_manifest()
    except SupplyGap as gap:
        print(str(gap))
        raise SystemExit(1)
    print("SUPPLY_VERIFIED")
