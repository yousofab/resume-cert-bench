"""Run and optionally check the deterministic simulator fixtures."""

import argparse
import json
from pathlib import Path

from .demo import report_json
from .pilot.figure import coverage_svg
from .pilot.study import study


def main():
    parser = argparse.ArgumentParser(prog="python -m resumecert")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="reproduce the engineering fixtures")
    demo.add_argument("--check", type=Path, help="compare with a recorded JSON file")
    pilot = commands.add_parser("pilot", help="run the matched development pilot")
    pilot.add_argument("--format", choices=("summary", "fixtures", "runs", "svg"), default="summary")
    pilot.add_argument("--check", type=Path, help="compare with a recorded output file")
    options = parser.parse_args()
    if options.command == "demo":
        output = report_json()
    else:
        summary, fixtures, runs = study()
        if options.format == "svg":
            output = coverage_svg(summary)
        else:
            value = {"summary": summary, "fixtures": fixtures, "runs": runs}[options.format]
            output = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if options.check is None:
        print(output, end="")
    elif options.check.read_bytes() == output.encode("utf-8"):
        print(f"{options.command}: output matches {options.check}")
    else:
        parser.exit(1, f"{options.command}: output mismatch: {options.check}\n")


if __name__ == "__main__":
    main()
