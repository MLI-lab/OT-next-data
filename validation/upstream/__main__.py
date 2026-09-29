"""Fetch and verify the shared pinned upstream checkouts."""
import argparse
from validation.upstream import setup

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['setup'])
    parser.parse_args()
    setup()
