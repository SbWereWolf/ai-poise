"""Independent immutable supply prerequisite, without a product version lock."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
from email.parser import Parser

SPEC = Path(__file__).with_name("fixtures") / "v2_supply_inputs.json"


def verify_supply():
    expected = json.loads(SPEC.read_text(encoding="utf-8"))
    selected = {}
    for item in expected["files"]:
        path = Path(os.environ[item["environment"]])
        if path.name != item["name"]:
            raise ValueError(
                f"Wrong selected filename: {item['name']}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item[
            "sha256"]:
            raise ValueError(
                f"Selected input digest mismatch: {item['name']}")
        selected[item["name"]] = path
    build = json.loads(
        selected["build-record.json"].read_text(encoding="utf-8"))
    manifest = json.loads(
        selected["manifest.json"].read_text(encoding="utf-8"))
    if manifest["build"] != build or build["exit_code"] != 0:
        raise ValueError(
            "Build record and delivery manifest disagree")
    if (manifest["distribution"], manifest["version"],
        manifest["requires_python"]) != (
            expected["distribution"], expected["version"],
            expected["requires_python"]
    ):
        raise ValueError("Wrong selected distribution metadata")
    if manifest["source_commit"] != expected["source_commit"] or \
            build["source_commit"] != expected["source_commit"]:
        raise ValueError("Wrong accepted source commit")
    for name, key in (("source.bundle", "source_bundle_sha256"),
                      (manifest["wheel"], "wheel_sha256")):
        digest = hashlib.sha256(
            selected[name].read_bytes()).hexdigest()
        if manifest[key] != digest or build[key] != digest:
            raise ValueError(f"Wrong provenance: {name}")
    for name, key in (("build.stdout", "stdout_sha256"),
                      ("build.stderr", "stderr_sha256")):
        if hashlib.sha256(selected[name].read_bytes()).hexdigest() != \
                build[key]:
            raise ValueError(f"Wrong full build stream: {name}")
    refs = subprocess.run(["git", "bundle", "list-heads",
                           str(selected["source.bundle"])],
                          capture_output=True, check=False)
    if refs.returncode != 0 or expected[
        "source_commit"].encode() not in refs.stdout:
        raise ValueError(
            "Source bundle does not expose the accepted commit")
    with zipfile.ZipFile(selected[manifest["wheel"]]) as wheel:
        metadata = Parser().parsestr(wheel.read(
            "environment_maintenance-0.2.1.dist-info/METADATA").decode(
            "utf-8"))
        if (metadata["Name"], metadata["Version"],
            metadata["Requires-Python"]) != (
                expected["distribution"], expected["version"],
                expected["requires_python"]
        ):
            raise ValueError(
                "Wheel metadata differs from accepted supply")
        modules = [name for name in wheel.namelist()
                   if name.startswith(
                "environment_maintenance/") and name.endswith(".py")]
        if len(modules) != 7 or any(
                name.startswith("poise/") for name in
                wheel.namelist()):
            raise ValueError(
                "Wrong independent package module inventory")


if __name__ == "__main__":
    try:
        verify_supply()
    except (OSError, ValueError, KeyError,
            zipfile.BadZipFile) as error:
        print(f"SUPPLY_INVALID: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("SUPPLY_VERIFIED")
