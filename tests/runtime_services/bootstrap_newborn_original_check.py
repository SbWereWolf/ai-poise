"""Observe one real historical pytest failure; Poise owns the outer invocation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET


def git(root, *arguments):
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True,
    ).stdout


def digest_groups(root):
    """Hash complete historical input bytes, separately by their test role."""
    groups = {name: {"files": 0, "hash": hashlib.sha256()}
              for name in ("sources", "tests", "fixtures")}
    for base in ("src", "tests", "config"):
        for path in sorted((root / base).rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if base == "src":
                category = "sources"
            elif base == "config" or "fixtures" in relative.parts:
                category = "fixtures"
            else:
                category = "tests"
            data = path.read_bytes()
            group = groups[category]
            group["hash"].update(relative.as_posix().encode() + b"\0")
            group["hash"].update(str(len(data)).encode() + b"\0" + data)
            group["files"] += 1
    for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "conftest.py"):
        path = root / name
        if path.is_file():
            data = path.read_bytes()
            group = groups["fixtures"]
            group["hash"].update(name.encode() + b"\0")
            group["hash"].update(str(len(data)).encode() + b"\0" + data)
            group["files"] += 1
    return {name: {"files": group["files"], "sha256": group["hash"].hexdigest()}
            for name, group in groups.items()}


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def observe(arguments, output, receipt):
    repository = Path(__file__).resolve().parents[2]
    commit = git(repository, "rev-parse", arguments.source_commit + "^{commit}").decode().strip()
    tree = git(repository, "rev-parse", arguments.source_commit + "^{tree}").decode().strip()
    if commit != arguments.source_commit or tree != arguments.source_tree:
        raise ValueError("The explicit historical commit/tree does not match Git.")
    selected = Path(arguments.test_file)
    if selected.is_absolute() or ".." in selected.parts:
        raise ValueError("The selected regression must be repository-relative.")
    source = output / "historical-source"
    source.mkdir()
    archive = git(repository, "archive", "--format=tar", commit)
    subprocess.run(["tar", "-xf", "-", "-C", str(source)], input=archive,
                   check=True, capture_output=True)
    original_inputs = digest_groups(source)
    original_test = (source / selected).read_bytes()
    reviewed_test = (repository / selected).read_bytes()
    (source / selected).write_bytes(reviewed_test)
    inputs = {
        "source_commit": commit,
        "source_tree": tree,
        "original_inputs": original_inputs,
        "original_test_sha256": hashlib.sha256(original_test).hexdigest(),
        "overlaid_test": arguments.test_file,
        "overlaid_test_sha256": hashlib.sha256(reviewed_test).hexdigest(),
        "executed_inputs": digest_groups(source),
    }
    if inputs["executed_inputs"]["sources"] != original_inputs["sources"]:
        raise ValueError("Historical runtime source changed while preparing the regression.")
    write_json(output / "historical-inputs.json", inputs)
    junit = output / "historical-junit.xml"
    node = arguments.test_file + "::" + arguments.node
    argv = [sys.executable, "-B", "-m", "pytest", "-q", "--tb=short",
            "-o", "junit_logging=all", node, "--junitxml=" + str(junit)]
    receipt.update(inputs=inputs, python=sys.version, executable=sys.executable,
                   argv=argv, cwd=str(source), selected=node)
    ran = subprocess.run(argv, cwd=source, text=True, capture_output=True)
    diagnostics = "STDOUT\n" + ran.stdout + "\nSTDERR\n" + ran.stderr
    (output / "historical-pytest.txt").write_text(diagnostics, encoding="utf-8")
    receipt.update(pytest_exit=ran.returncode, stdout=ran.stdout, stderr=ran.stderr)
    report = ET.parse(junit).getroot()
    cases = list(report.iter("testcase"))
    failures = list(report.iter("failure"))
    errors = list(report.iter("error"))
    skipped = list(report.iter("skipped"))
    source_marker = "POISE_SOURCE=" + str(source / "src/poise/__init__.py")
    state_marker = "TASK_AND_OWNERSHIP_UNCHANGED"
    captured = ET.tostring(report, encoding="unicode")
    exact_case = (len(cases) == 1 and cases[0].get("name") == arguments.node
                  and cases[0].get("classname") == arguments.test_file[:-3].replace("/", "."))
    exact_failure = (len(failures) == 1
                     and failures[0].get("message") == "KeyError: 'version'")
    conserved = state_marker in captured and source_marker in captured
    receipt.update(collected=len(cases), failure_count=len(failures),
                   error_count=len(errors), skipped_count=len(skipped),
                   exact_selected_case=exact_case, exact_key_error=exact_failure,
                   state_and_source_observed=conserved,
                   diagnostics=str(output / "historical-pytest.txt"), junit=str(junit))
    matched = (ran.returncode == 1 and exact_case and exact_failure
               and not errors and not skipped and conserved)
    receipt["status"] = "intended_original_failure" if matched else "not_the_declared_red"
    if matched:
        print("RED: original newborn bootstrap raises KeyError(version); Task and ownership unchanged")
        return 1
    print("NON_RED: historical pytest result does not match the declared failure and state proof")
    return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--test-file", required=True)
    parser.add_argument("--node", required=True)
    arguments = parser.parse_args()
    output = Path(os.environ["POISE_RUN_OUTPUT_DIR"]).resolve()
    receipt = {"status": "not_executed"}
    try:
        return observe(arguments, output, receipt)
    except Exception as error:
        receipt.update(status="non_red_error", error=repr(error))
        with (output / "historical-pytest.txt").open("a", encoding="utf-8") as diagnostics:
            traceback.print_exc(file=diagnostics)
        print("NON_RED: historical evidence setup or interpretation failed; inspect retained diagnostics")
        return 2
    finally:
        write_json(output / "historical-receipt.json", receipt)


if __name__ == "__main__":
    raise SystemExit(main())
