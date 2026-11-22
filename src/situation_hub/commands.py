"""Imports each workstream's command module so it can register in cli.COMMANDS."""

from situation_hub import tick  # noqa: F401
from situation_hub.cli import COMMANDS
from situation_hub.situation import command as situation_command

COMMANDS["situation"] = (
    "Print the current situation as JSON",
    situation_command.configure,
    situation_command.run,
)
