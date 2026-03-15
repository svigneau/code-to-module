#!/usr/bin/env python3
"""Index a BAM file."""
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    args = parser.parse_args()
    print(f"Indexing {args.input}")

if __name__ == "__main__":
    main()
