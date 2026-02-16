"""CLI commands for infralink."""

__all__ = ["cli"]


def __getattr__(name: str):
    if name == "cli":
        from infralink.cli.main import cli

        return cli
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
