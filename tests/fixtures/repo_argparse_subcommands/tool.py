#!/usr/bin/env python3
"""Argparse-based CLI with subcommands."""
import argparse


def main():
    parser = argparse.ArgumentParser(description="My tool")
    subparsers = parser.add_subparsers(dest="command")

    align_parser = subparsers.add_parser("align", help="Align reads")
    align_parser.add_argument("input")
    align_parser.add_argument("--reference", required=True)

    sort_parser = subparsers.add_parser("sort", help="Sort reads")
    sort_parser.add_argument("input")

    index_parser = subparsers.add_parser("index", help="Index BAM")
    index_parser.add_argument("input")

    _args = parser.parse_args()

if __name__ == "__main__":
    main()
