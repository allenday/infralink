"""Provider-neutral secret value and resolver contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn, Protocol, SupportsIndex, runtime_checkable


class SecretValue:
    """Hold plaintext behind an explicit reveal boundary.

    Python cannot guarantee zeroization: the underlying string remains in process
    memory until its normal lifetime ends.
    """

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if type(value) is not str:
            raise TypeError("SecretValue requires a string")
        self.__value = value

    def __str__(self) -> str:
        return "[REDACTED]"

    def __repr__(self) -> str:
        return "SecretValue([REDACTED])"

    def __format__(self, format_spec: str) -> NoReturn:
        raise TypeError("SecretValue requires explicit reveal()")

    def __bytes__(self) -> NoReturn:
        raise TypeError("SecretValue cannot be converted to bytes")

    def __iter__(self) -> NoReturn:
        raise TypeError("SecretValue is not iterable")

    def __copy__(self) -> NoReturn:
        raise TypeError("SecretValue cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise TypeError("SecretValue cannot be copied")

    def __getstate__(self) -> NoReturn:
        raise TypeError("SecretValue state cannot be extracted")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("SecretValue cannot be pickled")

    def reveal(self) -> str:
        """Return plaintext for an explicitly trusted in-process consumer."""
        return self.__value


@dataclass(frozen=True)
class SecretReference:
    """A provider-neutral secret reference and its declaration locations."""

    ref: str
    project: str | None
    locations: tuple[str, ...]
    required: bool = True


@dataclass(frozen=True)
class SecretAudit:
    """Provider-neutral availability metadata for a secret reference."""

    ref: str
    project: str | None
    present: bool | None
    accessible: bool | None
    error_code: str | None = None


@runtime_checkable
class SecretResolver(Protocol):
    """Resolve and audit secret references without exposing a provider."""

    def resolve(self, reference: SecretReference) -> SecretValue: ...

    def audit(self, references: list[SecretReference]) -> list[SecretAudit]: ...
