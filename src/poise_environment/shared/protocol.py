"""CLI presentation for Poise-owned catalog/v2 actions, not an engine."""

from dataclasses import dataclass
import json
from pathlib import Path
from string import Template
import sys
import traceback


@dataclass(frozen=True)
class Observation:
    state: str
    cause: str = ""
    detail: str = ""


class MaintenanceError(ValueError):
    def __init__(self, cause, detail):
        super().__init__(detail)
        self.cause = cause


def serve(parser, handler):
    parser.add_argument("--requirement-id", required=True)
    parser.add_argument("--action",
                        choices=("check", "install", "repair"),
                        required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--recommendations", required=True)
    args = parser.parse_args()
    base = {"schema": args.schema,
            "requirement_id": args.requirement_id,
            "action": args.action}
    try:
        advice = json.loads(
            Path(args.recommendations).read_text(encoding="utf-8"))[
            "entries"][args.requirement_id]
        try:
            observed = handler(args)
        except MaintenanceError as error:
            observed = Observation("unavailable", error.cause,
                                   str(error))
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            observed = Observation("unavailable", "unknown",
                                   str(error))
        if observed.detail:
            print(observed.detail, file=sys.stderr)
        recommendations = None
        if observed.state == "unavailable" or (
                args.action != "check" and observed.state != "satisfied"):
            recommendations = [Template(text).substitute(vars(args))
                               for text in advice[observed.cause]]
        if args.action == "check":
            response = {**base, "state": observed.state}
            if observed.state == "unavailable":
                response.update(cause=observed.cause,
                                recommendations=recommendations)
            code = 0
        elif observed.state == "satisfied":
            response = {**base, "outcome": "completed"}
            code = 0
        else:
            response = {**base, "outcome": "unavailable",
                        "cause": observed.cause,
                        "recommendations": recommendations}
            code = 2
        print(json.dumps(response, ensure_ascii=False))
        return code
    except (OSError, ValueError, KeyError) as error:
        print(f"Invalid action configuration: {error}",
              file=sys.stderr)
        return 2
