"""The build contract accepts compatible tools, not one exact artifact."""
from pathlib import Path
import tomllib
from packaging.requirements import Requirement
import pytest

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.parametrize("version", ["80.9.0", "82.0.1"])
def test_build_contract_accepts_compatible_setuptools(version):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    requirement, = [Requirement(item) for item in config["build-system"]["requires"]
                    if Requirement(item).name == "setuptools"]
    assert requirement.specifier.contains(version)

def test_build_contract_keeps_its_supported_lower_bound():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    requirement, = [Requirement(item) for item in config["build-system"]["requires"]
                    if Requirement(item).name == "setuptools"]
    assert not requirement.specifier.contains("80.8.0")
