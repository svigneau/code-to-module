"""chaintool — a simple bioinformatics read-processing tool."""

import argparse
import sys


def run(args: argparse.Namespace) -> None:
    """Process reads and produce a report."""
    print(f"Processing reads from {args.input}, writing to {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="chaintool: process sequencing reads"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Process reads")
    run_parser.add_argument("--input", required=True, help="Input FASTQ file")
    run_parser.add_argument("--output", required=True, help="Output report file")
    run_parser.add_argument("--threads", type=int, default=1, help="Threads")

    parsed = parser.parse_args()
    if parsed.command == "run":
        run(parsed)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
