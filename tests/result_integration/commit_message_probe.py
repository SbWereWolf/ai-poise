"""A deterministic smallest RED observation, never a catch-all test adapter."""
import json
from pathlib import Path
import sys

from poise.modules.foundation.errors import DomainError
from poise.modules.result_integration.domain import IntegrationIntent


def main():
    contract = json.loads(
        (Path(__file__).parent / "fixtures" / "commit_message_contract.json").read_text()
    )
    try:
        actual = IntegrationIntent.parse(contract["input"])
    except DomainError as error:
        if str(error) != "integrate input requires exact intent and resolutions fields":
            raise
        print("RED: explicit integration message rejected")
        return 1
    assert actual.identity() == contract["expected_identity"]
    print("PASS: explicit integration message preserved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
