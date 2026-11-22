"""Command-line entry point. Workstreams register subcommands in COMMANDS."""
from __future__ import annotations

import argparse
from collections.abc import Callable

# name -> (help, configure(parser), run(args) -> int). Each workstream adds its own entries
# from its own module, imported below, so the modules never edit each other's files.
COMMANDS: dict[str, tuple[str, Callable[[argparse.ArgumentParser], None], Callable[[argparse.Namespace], int]]] = {}


def main(argv: list[str] | None = None) -> int:
    from situation_hub import commands  # noqa: F401  (registers workstream commands)

    parser = argparse.ArgumentParser(prog="situation-hub")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (help_text, configure, _run) in sorted(COMMANDS.items()):
        configure(sub.add_parser(name, help=help_text))
    args = parser.parse_args(argv)
    return COMMANDS[args.command][2](args)
