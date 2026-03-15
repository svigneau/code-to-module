#!/usr/bin/env python3
"""Align reads to a reference genome."""
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()
    print(f"Aligning {args.input} to {args.reference}")

if __name__ == "__main__":
    main()
