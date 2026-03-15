import argparse


def main() -> None:
    """Entry point for megtool."""
    parser = argparse.ArgumentParser(description="Megtool with exclusive modes")
    meg = parser.add_mutually_exclusive_group()
    meg.add_argument("--align", type=argparse.FileType("r"), help="Align reads BAM input")
    meg.add_argument("--sort", type=argparse.FileType("r"), help="Sort BAM input file")
    parser.add_argument("--output", required=True, help="Output file path")
    _args = parser.parse_args()


if __name__ == "__main__":
    main()
