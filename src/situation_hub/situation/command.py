"""Command-line access to the situation snapshot."""

import argparse

from situation_hub.situation import encode_snapshot, read_situation


def configure(parser: argparse.ArgumentParser) -> None:
    """The situation command takes no arguments."""


def run(args: argparse.Namespace) -> int:
    print(encode_snapshot(read_situation()))
    return 0
