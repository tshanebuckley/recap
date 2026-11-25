"""Exceptions raised by the recap library. The command line turns them into messages and exit codes."""


class RecapError(Exception):
    """Base class for recap errors."""


class ConfigError(RecapError):
    """The configuration is invalid or refers to missing files."""


class Cancelled(RecapError):
    """The user cancelled (a Fiji dialog, the spot check, or a cancel callback)."""
