"""Local operator configuration shared by CLI and Agent Surface operations.

This configuration chooses the checkout an operator intends to inspect.  It
does not provide desired state and never overrides a host's configured
registry revision.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CONFIG_ENVVAR = "INFRALINK_CONFIG"
_EVIDENCE_KEY_ID = re.compile(r"^[a-z][a-z0-9-]{0,127}$")


class OperatorConfigError(ValueError):
    """The local operator configuration cannot be read as one registry selector."""


class FleetPrometheusEvidenceConfig(BaseModel):
    """Private local selection for the read-only fleet evidence artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_path: str = Field(min_length=1)
    trusted_public_keys: dict[str, str] = Field(min_length=1)

    @field_validator("artifact_path")
    @classmethod
    def require_absolute_artifact_path(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("artifact_path must be absolute")
        return value

    @field_validator("trusted_public_keys")
    @classmethod
    def require_nonblank_key_map(cls, value: dict[str, str]) -> dict[str, str]:
        for key, encoded in value.items():
            if _EVIDENCE_KEY_ID.fullmatch(key) is None or not encoded or not encoded.strip():
                raise ValueError("trusted_public_keys entries must be nonblank")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("trusted_public_keys values must be base64") from None
            if len(raw) != 32:
                raise ValueError("trusted_public_keys values must be raw Ed25519 public keys")
        return value


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


def configured_fleet_prometheus_evidence() -> FleetPrometheusEvidenceConfig | None:
    """Load the only local selector for bounded fleet evidence."""

    path = operator_config_path()
    if not path.is_file():
        return None
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(value, dict):
            raise ValueError("operator config must be a mapping")
        configured = value.get("fleet_prometheus_evidence")
        if configured is None:
            return None
        return FleetPrometheusEvidenceConfig.model_validate(configured)
    except (OSError, ValueError, ValidationError, yaml.YAMLError) as error:
        raise OperatorConfigError(str(path)) from error
