"""Hosted, read-only Bitwarden Secrets Manager adapter.

Production configuration is restricted to Bitwarden's hosted endpoints. The
SDK does not expose transport controls that would make arbitrary origins safe.
"""

from __future__ import annotations

import importlib
import ipaddress
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

from infralink.__about__ import __version__
from infralink.secrets.base import SecretAudit, SecretReference, SecretValue

HOSTED_API_URL = "https://api.bitwarden.com"
HOSTED_IDENTITY_URL = "https://identity.bitwarden.com"
_FAKE_TOKEN = "INFRALINK_FAKE_BWS_TOKEN"
_FORBIDDEN_ENVIRONMENT_KEYS = frozenset({"BWS_API_URL", "BWS_IDENTITY_URL", "BWS_TRUSTED_HOSTS"})
_MISSING = object()


class BwsErrorCode(str, Enum):
    """Safe categories for adapter failures."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_AUTHENTICATION_FAILED = "provider_authentication_failed"
    PROVIDER_AUTHORIZATION_FAILED = "provider_authorization_failed"
    PROVIDER_TIMEOUT = "provider_timeout"


class BwsConfigurationError(Exception):
    """Report an invalid adapter configuration without sensitive details."""


class BwsProviderError(Exception):
    """Report a provider failure using only a stable safe category."""

    def __init__(self, code: BwsErrorCode) -> None:
        self.code = code
        super().__init__(code.value)

    def __reduce__(
        self,
    ) -> tuple[type[BwsProviderError], tuple[BwsErrorCode]]:
        return (type(self), (self.code,))


class _AuthClient(Protocol):
    def login_access_token(self, token: str) -> object: ...


class _ProjectsClient(Protocol):
    def list(self, organization_id: str) -> object: ...


class _SecretsClient(Protocol):
    def list(self, organization_id: str) -> object: ...

    def get(self, secret_id: str) -> object: ...


class _SdkClient(Protocol):
    def auth(self) -> _AuthClient: ...

    def projects(self) -> _ProjectsClient: ...

    def secrets(self) -> _SecretsClient: ...


class SdkFactory(Protocol):
    """Construct a configured SDK client."""

    def __call__(self, config: BwsConfig) -> _SdkClient: ...


def _is_loopback_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
        if hostname == "localhost":
            return True
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _safe_attr(value: Any, name: str) -> Any:
    try:
        return getattr(value, name)
    except Exception:
        return _MISSING


@dataclass(frozen=True)
class BwsConfig:
    """Immutable connection settings with a redacted credential."""

    access_token: str = field(repr=False)
    organization_id: str
    api_url: str = HOSTED_API_URL
    identity_url: str = HOSTED_IDENTITY_URL
    test_only: bool = False

    def __post_init__(self) -> None:
        if not self.access_token or not self.organization_id:
            raise BwsConfigurationError("BWS_ACCESS_TOKEN and BWS_ORGANIZATION_ID are required")
        if self.test_only:
            if self.access_token != _FAKE_TOKEN:
                raise BwsConfigurationError("test configuration requires the fake token")
            if not _is_loopback_url(self.api_url) or not _is_loopback_url(self.identity_url):
                raise BwsConfigurationError("test configuration requires loopback endpoints")
        elif self.api_url != HOSTED_API_URL or self.identity_url != HOSTED_IDENTITY_URL:
            raise BwsConfigurationError("production configuration requires hosted endpoints")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> BwsConfig:
        """Load required credentials while rejecting all endpoint overrides."""
        token = env.get("BWS_ACCESS_TOKEN", "")
        organization_id = env.get("BWS_ORGANIZATION_ID", "")
        if not token or not organization_id:
            raise BwsConfigurationError("BWS_ACCESS_TOKEN and BWS_ORGANIZATION_ID are required")
        if _FORBIDDEN_ENVIRONMENT_KEYS.intersection(env):
            raise BwsConfigurationError("endpoint overrides are unsupported")
        return cls(access_token=token, organization_id=organization_id)

    @classmethod
    def for_test(
        cls,
        *,
        access_token: str,
        organization_id: str,
        api_url: str,
        identity_url: str,
    ) -> BwsConfig:
        """Construct a loopback-only configuration for an injected fake SDK."""
        return cls(
            access_token=access_token,
            organization_id=organization_id,
            api_url=api_url,
            identity_url=identity_url,
            test_only=True,
        )


def _default_sdk_factory(config: BwsConfig) -> _SdkClient:
    sdk = importlib.import_module("bitwarden_sdk")
    settings = sdk.client_settings_from_dict(
        {
            "apiUrl": config.api_url,
            "identityUrl": config.identity_url,
            "deviceType": sdk.DeviceType.SDK,
            "userAgent": f"infralink/{__version__}",
        }
    )
    return cast(_SdkClient, sdk.BitwardenClient(settings))


class BwsSecretResolver:
    """Read declared project-scoped references through the Bitwarden SDK."""

    __slots__ = ("_client", "_config")

    def __init__(
        self,
        *,
        config: BwsConfig,
        sdk_factory: SdkFactory | None = None,
    ) -> None:
        if config.test_only and (sdk_factory is None or sdk_factory is _default_sdk_factory):
            raise BwsConfigurationError(
                "test configuration requires an explicitly injected SDK factory"
            )
        factory = _default_sdk_factory if sdk_factory is None else sdk_factory
        failure_code: BwsErrorCode | None = None
        client: Any = _MISSING
        login: object = _MISSING
        try:
            client = factory(config)
            login = client.auth().login_access_token(config.access_token)
        except TimeoutError:
            failure_code = BwsErrorCode.PROVIDER_TIMEOUT
        except Exception:
            failure_code = BwsErrorCode.PROVIDER_UNAVAILABLE
        if failure_code is not None:
            raise BwsProviderError(failure_code)
        login_succeeded = _safe_attr(login, "success")
        if type(login_succeeded) is not bool:
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        if not login_succeeded:
            raise BwsProviderError(BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED)
        self._config = config
        self._client = cast(_SdkClient, client)

    def _call(self, operation: Callable[[], Any]) -> Any:
        result: Any = _MISSING
        failure_code: BwsErrorCode | None = None
        try:
            result = operation()
        except TimeoutError:
            failure_code = BwsErrorCode.PROVIDER_TIMEOUT
        except Exception:
            failure_code = BwsErrorCode.PROVIDER_UNAVAILABLE
        if failure_code is not None:
            raise BwsProviderError(failure_code)
        return result

    @staticmethod
    def _list_payload(response: Any, failure_code: BwsErrorCode) -> list[Any]:
        succeeded = _safe_attr(response, "success")
        if type(succeeded) is not bool:
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        if not succeeded:
            raise BwsProviderError(failure_code)
        items = _safe_attr(_safe_attr(response, "data"), "data")
        if type(items) is not list:
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        return items

    @staticmethod
    def _normalize_id(value: Any) -> str:
        if isinstance(value, UUID):
            return str(value)
        if type(value) is str and value:
            return value
        raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)

    def _accessible_projects(self, configured_projects: set[str]) -> set[str]:
        response = self._call(lambda: self._client.projects().list(self._config.organization_id))
        items = self._list_payload(response, BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED)
        project_ids: set[str] = set()
        for item in items:
            project_id = self._normalize_id(_safe_attr(item, "id"))
            if project_id in project_ids:
                raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
            project_ids.add(project_id)
        accessible = configured_projects.intersection(project_ids)
        if not accessible:
            raise BwsProviderError(BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED)
        return accessible

    def _identifier_index(self) -> dict[tuple[str, str], str]:
        response = self._call(lambda: self._client.secrets().list(self._config.organization_id))
        items = self._list_payload(response, BwsErrorCode.PROVIDER_UNAVAILABLE)
        identifiers: dict[tuple[str, str], str] = {}
        for item in items:
            secret_id = self._normalize_id(_safe_attr(item, "id"))
            key = _safe_attr(item, "key")
            project_ids = _safe_attr(item, "project_ids")
            if type(key) is not str or type(project_ids) is not list:
                raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
            for raw_project_id in project_ids:
                project_id = self._normalize_id(raw_project_id)
                match = (project_id, key)
                if match in identifiers:
                    raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
                identifiers[match] = secret_id
        return identifiers

    @staticmethod
    def _configured_projects(references: list[SecretReference]) -> set[str]:
        projects: set[str] = set()
        for reference in references:
            if reference.project is None:
                raise BwsConfigurationError("Bitwarden references require an explicit project")
            projects.add(reference.project)
        return projects

    def audit(self, references: list[SecretReference]) -> list[SecretAudit]:
        """Check declared references using metadata only."""
        if not references:
            return []
        configured_projects = self._configured_projects(references)
        accessible_projects = self._accessible_projects(configured_projects)
        identifiers = self._identifier_index()
        audits: list[SecretAudit] = []
        for reference in references:
            project_id = cast(str, reference.project)
            if project_id not in accessible_projects:
                audits.append(
                    SecretAudit(
                        ref=reference.ref,
                        project=project_id,
                        present=None,
                        accessible=False,
                        error_code="project_unavailable",
                    )
                )
            elif (project_id, reference.ref) not in identifiers:
                audits.append(
                    SecretAudit(
                        ref=reference.ref,
                        project=project_id,
                        present=None,
                        accessible=False,
                        error_code="unavailable_or_missing",
                    )
                )
            else:
                audits.append(
                    SecretAudit(
                        ref=reference.ref,
                        project=project_id,
                        present=True,
                        accessible=True,
                    )
                )
        return audits

    def resolve(self, reference: SecretReference) -> SecretValue:
        """Resolve one declared project-scoped reference to an opaque value."""
        configured_projects = self._configured_projects([reference])
        self._accessible_projects(configured_projects)
        identifiers = self._identifier_index()
        project_id = cast(str, reference.project)
        secret_id = identifiers.get((project_id, reference.ref))
        if secret_id is None:
            raise BwsProviderError(BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED)
        response = self._call(lambda: self._client.secrets().get(secret_id))
        succeeded = _safe_attr(response, "success")
        if type(succeeded) is not bool:
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        if not succeeded:
            raise BwsProviderError(BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED)
        value = _safe_attr(_safe_attr(response, "data"), "value")
        if type(value) is not str:
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        return SecretValue(value)
