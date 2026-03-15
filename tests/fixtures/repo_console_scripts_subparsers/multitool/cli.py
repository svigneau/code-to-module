import argparse


def main() -> None:
    """Entry point for multitool."""
    parser = argparse.ArgumentParser(description="Multitool bioinformatics suite")
    subparsers = parser.add_subparsers(dest="command")

    align_parser = subparsers.add_parser("align", help="Align reads to reference")
    align_parser.add_argument("--reads", required=True, help="Input reads file")
    align_parser.add_argument("--reference", required=True, help="Reference genome")
    align_parser.add_argument("--output", required=True, help="Output BAM file")

    sort_parser = subparsers.add_parser("sort", help="Sort BAM file")
    sort_parser.add_argument("--input", required=True, help="Input BAM file")
    sort_parser.add_argument("--output", required=True, help="Output sorted BAM")

    _args = parser.parse_args()


if __name__ == "__main__":
    main()
