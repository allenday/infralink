"""Local operator configuration shared by CLI and Agent Surface operations.

This configuration chooses the checkout an operator intends to inspect.  It
does not provide desired state and never overrides a host's configured
registry revision.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

CONFIG_ENVVAR = "INFRALINK_CONFIG"


class OperatorConfigError(ValueError):
    """The local operator configuration cannot be read as one registry selector."""


def operator_config_path() -> Path:
    """Return the explicit or conventional local operator configuration path."""
    configured = os.environ.get(CONFIG_ENVVAR)
    if configured:
        return Path(configured).expanduser()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "infralink" / "config.yml"


def configured_registry() -> Path | None:
    """Resolve the optional local registry checkout selector."""
    path = operator_config_path()
    if not path.is_file():
        return None
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise OperatorConfigError(str(path)) from error
    if not isinstance(value, dict) or not isinstance(value.get("registry"), str):
        raise OperatorConfigError(str(path))
    selected = Path(value["registry"]).expanduser()
    return selected if selected.is_absolute() else path.parent / selected
