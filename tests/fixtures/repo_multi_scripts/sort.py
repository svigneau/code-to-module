#!/usr/bin/env python3
"""Sort a BAM file."""
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(f"Sorting {args.input}")

if __name__ == "__main__":
    main()
