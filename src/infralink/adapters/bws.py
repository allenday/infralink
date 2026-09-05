"""Hosted, read-only Bitwarden Secrets Manager adapter.

Production configuration is restricted to Bitwarden's hosted endpoints. The
SDK does not expose transport controls that would make arbitrary origins safe.
"""

from __future__ import annotations

import importlib
import ipaddress
import os
from collections.abc import Callable, Mapping
from dataclasses import InitVar, dataclass, field
from enum import Enum
from typing import Any, NoReturn, Protocol, SupportsIndex, cast
from urllib.parse import urlsplit
from uuid import UUID

from infralink.__about__ import __version__
from infralink.secrets.base import SecretAudit, SecretReference, SecretValue

HOSTED_API_URL = "https://api.bitwarden.com"
HOSTED_IDENTITY_URL = "https://identity.bitwarden.com"
_FAKE_TOKEN = "INFRALINK_FAKE_BWS_TOKEN"
_FORBIDDEN_ENVIRONMENT_KEYS = frozenset({"BWS_API_URL", "BWS_IDENTITY_URL", "BWS_TRUSTED_HOSTS"})
_MISSING = object()


def _reject_bws_config_state(_config: object) -> NoReturn:
    raise TypeError("BwsConfig state cannot be extracted")


def _reject_bws_config_state_restore(_config: object, _state: object) -> NoReturn:
    raise TypeError("BwsConfig state cannot be restored")


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
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or port is None
            or not 1 <= port <= 65535
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


@dataclass(frozen=True, slots=True)
class BwsConfig:
    """Immutable connection settings with a redacted credential."""

    access_token: InitVar[str]
    organization_id: str
    api_url: str = HOSTED_API_URL
    identity_url: str = HOSTED_IDENTITY_URL
    test_only: bool = False
    _access_token: SecretValue = field(init=False, repr=False, compare=False)

    def __post_init__(self, access_token: str) -> None:
        if not access_token or not self.organization_id:
            raise BwsConfigurationError("BWS_ACCESS_TOKEN and BWS_ORGANIZATION_ID are required")
        try:
            organization_id = str(UUID(self.organization_id))
        except (AttributeError, TypeError, ValueError):
            raise BwsConfigurationError("BWS organization identity must be a UUID") from None
        if self.test_only:
            if access_token != _FAKE_TOKEN:
                raise BwsConfigurationError("test configuration requires the fake token")
            if not _is_loopback_url(self.api_url) or not _is_loopback_url(self.identity_url):
                raise BwsConfigurationError("test configuration requires loopback endpoints")
        elif self.api_url != HOSTED_API_URL or self.identity_url != HOSTED_IDENTITY_URL:
            raise BwsConfigurationError("production configuration requires hosted endpoints")
        object.__setattr__(self, "organization_id", organization_id)
        object.__setattr__(self, "_access_token", SecretValue(access_token))

    def __copy__(self) -> NoReturn:
        raise TypeError("BwsConfig cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise TypeError("BwsConfig cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("BwsConfig cannot be pickled")

    def _reveal_access_token(self) -> str:
        return self._access_token.reveal()

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


# Python 3.10 replaces both guards when frozen dataclass slots are generated.
_bws_config_type = cast(Any, BwsConfig)
_bws_config_type.__getstate__ = _reject_bws_config_state
_bws_config_type.__setstate__ = _reject_bws_config_state_restore


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
    """Read ordered declared project-name references through the Bitwarden SDK."""

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
        client = self._call(
            lambda: factory(config),
            BwsErrorCode.PROVIDER_UNAVAILABLE,
        )
        login = self._call(
            lambda: client.auth().login_access_token(config._reveal_access_token()),
            BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED,
        )
        login_succeeded = _safe_attr(login, "success")
        if type(login_succeeded) is not bool:
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        if not login_succeeded:
            raise BwsProviderError(BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED)
        authenticated = _safe_attr(_safe_attr(login, "data"), "authenticated")
        if type(authenticated) is not bool:
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        if not authenticated:
            raise BwsProviderError(BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED)
        self._config = config
        self._client = cast(_SdkClient, client)

    @staticmethod
    def _call(operation: Callable[[], Any], exception_code: BwsErrorCode) -> Any:
        failure_code: BwsErrorCode | None = None
        result: Any = _MISSING
        try:
            result = operation()
        except TimeoutError:
            failure_code = BwsErrorCode.PROVIDER_TIMEOUT
        except Exception:
            failure_code = exception_code
        if failure_code is not None:
            raise BwsProviderError(failure_code) from None
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
        if type(value) is str:
            try:
                return str(UUID(value))
            except ValueError:
                pass
        raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE) from None

    def _accessible_projects(self, configured_projects: set[str]) -> dict[str, str]:
        response = self._call(
            lambda: self._client.projects().list(self._config.organization_id),
            BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        )
        items = self._list_payload(response, BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED)
        accessible: dict[str, str] = {}
        for item in items:
            project_id = self._normalize_id(_safe_attr(item, "id"))
            organization_id = self._normalize_id(_safe_attr(item, "organization_id"))
            name = _safe_attr(item, "name")
            if organization_id != self._config.organization_id or type(name) is not str or not name:
                raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
            if name in accessible or project_id in accessible.values():
                raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
            if name in configured_projects:
                accessible[name] = project_id
        return accessible

    def _identifier_index(self) -> dict[tuple[str, str], str]:
        response = self._call(
            lambda: self._client.secrets().list(self._config.organization_id),
            BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        )
        items = self._list_payload(response, BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED)
        identifiers: dict[tuple[str, str], str] = {}
        for item in items:
            secret_id = self._normalize_id(_safe_attr(item, "id"))
            key = _safe_attr(item, "key")
            organization_id = self._normalize_id(_safe_attr(item, "organization_id"))
            project_ids = _safe_attr(item, "project_ids")
            if (
                type(key) is not str
                or organization_id != self._config.organization_id
                or type(project_ids) is not list
            ):
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
            for project in reference.projects:
                if type(project) is not str or not project:
                    raise BwsConfigurationError("Bitwarden references require nonempty projects")
                projects.add(project)
        return projects

    def audit(self, references: list[SecretReference]) -> list[SecretAudit]:
        """Check declared references using metadata only."""
        if not references:
            return []
        configured_projects = self._configured_projects(references)
        accessible_projects = self._accessible_projects(configured_projects)
        identifiers = self._identifier_index() if accessible_projects else {}
        audits: list[SecretAudit] = []
        for reference in references:
            selected_project = next(
                (
                    project
                    for project in reference.projects
                    if (project_id := accessible_projects.get(project)) is not None
                    and (project_id, reference.ref) in identifiers
                ),
                None,
            )
            if selected_project is not None:
                audits.append(
                    SecretAudit(
                        ref=reference.ref,
                        project=selected_project,
                        present=True,
                        accessible=True,
                    )
                )
            elif not any(project in accessible_projects for project in reference.projects):
                audits.append(
                    SecretAudit(
                        ref=reference.ref,
                        project=None,
                        present=None,
                        accessible=False,
                        error_code="project_unavailable",
                    )
                )
            else:
                audits.append(
                    SecretAudit(
                        ref=reference.ref,
                        project=None,
                        present=None,
                        accessible=False,
                        error_code="unavailable_or_missing",
                    )
                )
        return audits

    def resolve(self, reference: SecretReference) -> SecretValue:
        """Resolve one declared project-scoped reference to an opaque value."""
        configured_projects = self._configured_projects([reference])
        if not configured_projects:
            raise BwsConfigurationError("Bitwarden references require nonempty projects")
        accessible_projects = self._accessible_projects(configured_projects)
        identifiers = self._identifier_index()
        selected = next(
            (
                (project, project_id, identifiers[(project_id, reference.ref)])
                for project in reference.projects
                if (project_id := accessible_projects.get(project)) is not None
                and (project_id, reference.ref) in identifiers
            ),
            None,
        )
        if selected is None:
            raise BwsProviderError(BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED)
        _project, project_id, secret_id = selected
        response = self._call(
            lambda: self._client.secrets().get(secret_id),
            BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        )
        succeeded = _safe_attr(response, "success")
        if type(succeeded) is not bool:
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        if not succeeded:
            raise BwsProviderError(BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED)
        data = _safe_attr(response, "data")
        response_id = self._normalize_id(_safe_attr(data, "id"))
        response_key = _safe_attr(data, "key")
        response_organization_id = self._normalize_id(_safe_attr(data, "organization_id"))
        response_project_id = self._normalize_id(_safe_attr(data, "project_id"))
        if (
            response_id != secret_id
            or response_key != reference.ref
            or response_organization_id != self._config.organization_id
            or response_project_id != project_id
        ):
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        value = _safe_attr(data, "value")
        if type(value) is not str:
            raise BwsProviderError(BwsErrorCode.PROVIDER_UNAVAILABLE)
        return SecretValue(value)
