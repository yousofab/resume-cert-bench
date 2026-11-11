"""Run and optionally check the deterministic simulator fixtures."""

import argparse
from pathlib import Path

from .demo import report_json


def main():
    parser = argparse.ArgumentParser(prog="python -m resumecert")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="reproduce the engineering fixtures")
    demo.add_argument("--check", type=Path, help="compare with a recorded JSON file")
    options = parser.parse_args()
    output = report_json()
    if options.check is None:
        print(output, end="")
    elif options.check.read_bytes() == output.encode("utf-8"):
        print(f"Simulator fixtures match {options.check}")
    else:
        parser.exit(1, f"Simulator fixture mismatch: {options.check}\n")


if __name__ == "__main__":
    main()
