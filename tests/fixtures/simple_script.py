#!/usr/bin/env python3
"""A simple script with no CLI structure."""


def compute(data):
    return data * 2


def main():
    result = compute(42)
    print(result)


if __name__ == "__main__":
    main()
