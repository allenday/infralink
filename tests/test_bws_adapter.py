from __future__ import annotations

import copy
import importlib
import json
import pickle
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import FrozenInstanceError, asdict
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from infralink.adapters.bws import (
    BwsConfig,
    BwsConfigurationError,
    BwsErrorCode,
    BwsProviderError,
    BwsSecretResolver,
    _default_sdk_factory,
)
from infralink.secrets.base import SecretAudit, SecretReference, SecretValue

HOSTED_API_URL = "https://api.bitwarden.com"
HOSTED_IDENTITY_URL = "https://identity.bitwarden.com"
ORGANIZATION_ID = "11111111-1111-1111-1111-111111111111"
FAKE_TOKEN = "INFRALINK_FAKE_BWS_TOKEN"
CANARY = "canary-secret-material"
PROJECT_A = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_B = UUID("33333333-3333-3333-3333-333333333333")
SECRET_A = UUID("44444444-4444-4444-4444-444444444444")
SECRET_B = UUID("55555555-5555-5555-5555-555555555555")
MIXED_ORGANIZATION_ID = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"


def test_from_env_requires_token_and_organization() -> None:
    with pytest.raises(BwsConfigurationError, match="required"):
        BwsConfig.from_env({})
    with pytest.raises(BwsConfigurationError, match="required"):
        BwsConfig.from_env({"BWS_ACCESS_TOKEN": CANARY})
    with pytest.raises(BwsConfigurationError, match="required"):
        BwsConfig.from_env({"BWS_ORGANIZATION_ID": ORGANIZATION_ID})


def test_hosted_endpoints_are_fixed_and_config_is_frozen_and_redacted() -> None:
    config = BwsConfig.from_env(
        {
            "BWS_ACCESS_TOKEN": CANARY,
            "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
        }
    )

    assert config.api_url == HOSTED_API_URL
    assert config.identity_url == HOSTED_IDENTITY_URL
    assert config.test_only is False
    assert CANARY not in repr(config)
    assert CANARY not in str(config)
    with pytest.raises(FrozenInstanceError):
        config.api_url = "https://example.com"  # type: ignore[misc]


def test_config_blocks_credential_extraction_protocols() -> None:
    config = BwsConfig.from_env(
        {
            "BWS_ACCESS_TOKEN": CANARY,
            "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
        }
    )
    operations: list[Callable[[], object]] = [
        lambda: asdict(config),
        lambda: vars(config),
        lambda: pickle.dumps(config),
        lambda: copy.copy(config),
        lambda: copy.deepcopy(config),
        lambda: config.__getstate__(),
    ]

    for operation in operations:
        with pytest.raises((BwsConfigurationError, TypeError)) as caught:
            operation()
        assert CANARY not in str(caught.value)
        assert CANARY not in repr(caught.value)
    with pytest.raises(TypeError):
        json.dumps(config)
    assert not hasattr(config, "access_token")
    assert not hasattr(config, "__dict__")


@pytest.mark.parametrize(
    "name",
    ["BWS_API_URL", "BWS_IDENTITY_URL", "BWS_TRUSTED_HOSTS"],
)
@pytest.mark.parametrize("value", ["", "http://127.0.0.1:8080"])
def test_from_env_rejects_endpoint_override_presence(name: str, value: str) -> None:
    with pytest.raises(BwsConfigurationError, match="overrides"):
        BwsConfig.from_env(
            {
                "BWS_ACCESS_TOKEN": CANARY,
                "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
                name: value,
            }
        )


def test_direct_production_construction_requires_exact_hosted_urls() -> None:
    with pytest.raises(BwsConfigurationError, match="required"):
        BwsConfig(access_token=CANARY, organization_id="")
    with pytest.raises(BwsConfigurationError, match="hosted"):
        BwsConfig(
            access_token=CANARY,
            organization_id=ORGANIZATION_ID,
            api_url="https://example.com",
        )


def test_config_normalizes_organization_uuid_and_rejects_invalid_identity() -> None:
    config = BwsConfig(access_token=CANARY, organization_id=MIXED_ORGANIZATION_ID)

    assert config.organization_id == MIXED_ORGANIZATION_ID.lower()
    with pytest.raises(BwsConfigurationError, match="organization"):
        BwsConfig(access_token=CANARY, organization_id="not-a-uuid")


@pytest.mark.parametrize(
    ("token", "api_url", "identity_url"),
    [
        ("not-the-literal", "http://127.0.0.1:8080", "http://localhost:8081"),
        (FAKE_TOKEN, "https://example.com", "http://localhost:8081"),
        (FAKE_TOKEN, "http://user@localhost:8080", "http://localhost:8081"),
        (FAKE_TOKEN, "http://localhost:8080/path", "http://localhost:8081"),
        (FAKE_TOKEN, "http://localhost:8080?query=x", "http://localhost:8081"),
        (FAKE_TOKEN, "http://localhost:8080#fragment", "http://localhost:8081"),
        (FAKE_TOKEN, "ftp://localhost:8080", "http://localhost:8081"),
        (FAKE_TOKEN, "http://localhost", "http://localhost:8081"),
        (FAKE_TOKEN, "http://localhost:", "http://localhost:8081"),
        (FAKE_TOKEN, "http://localhost:0", "http://localhost:8081"),
        (FAKE_TOKEN, "http://localhost:65536", "http://localhost:8081"),
        (FAKE_TOKEN, "http://localhost:not-a-port", "http://localhost:8081"),
    ],
)
def test_for_test_requires_literal_token_and_strict_loopback_urls(
    token: str, api_url: str, identity_url: str
) -> None:
    with pytest.raises(BwsConfigurationError):
        BwsConfig.for_test(
            access_token=token,
            organization_id=ORGANIZATION_ID,
            api_url=api_url,
            identity_url=identity_url,
        )


def test_for_test_accepts_http_and_https_loopback_hosts() -> None:
    config = BwsConfig.for_test(
        access_token=FAKE_TOKEN,
        organization_id=ORGANIZATION_ID,
        api_url="http://127.0.0.1:8080",
        identity_url="https://[::1]:8081",
    )

    assert config.test_only is True


class FakeAuth:
    def __init__(self, response: object) -> None:
        self.response = response
        self.tokens: list[str] = []

    def login_access_token(self, token: str) -> object:
        self.tokens.append(token)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class FakeProjects:
    def __init__(self, response: object) -> None:
        self.response = response
        self.organizations: list[str] = []

    def list(self, organization_id: str) -> object:
        self.organizations.append(organization_id)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class FakeSecrets:
    def __init__(self, list_response: object, get_response: object) -> None:
        self.list_response = list_response
        self.get_response = get_response
        self.organizations: list[str] = []
        self.get_calls: list[str] = []

    def list(self, organization_id: str) -> object:
        self.organizations.append(organization_id)
        if isinstance(self.list_response, BaseException):
            raise self.list_response
        return self.list_response

    def get(self, secret_id: str) -> object:
        self.get_calls.append(secret_id)
        if isinstance(self.get_response, BaseException):
            raise self.get_response
        return self.get_response


class FakeClient:
    def __init__(
        self,
        *,
        login: object = SimpleNamespace(
            success=True,
            data=SimpleNamespace(authenticated=True),
        ),
        projects: object = SimpleNamespace(
            success=True,
            data=SimpleNamespace(
                data=[
                    SimpleNamespace(id=PROJECT_A, organization_id=UUID(ORGANIZATION_ID)),
                    SimpleNamespace(id=PROJECT_B, organization_id=UUID(ORGANIZATION_ID)),
                ]
            ),
        ),
        identifiers: object = SimpleNamespace(
            success=True,
            data=SimpleNamespace(
                data=[
                    SimpleNamespace(
                        id=SECRET_A,
                        key="db_password",
                        organization_id=UUID(ORGANIZATION_ID),
                        project_ids=[PROJECT_A],
                    ),
                    SimpleNamespace(
                        id=SECRET_B,
                        key="api_key",
                        organization_id=UUID(ORGANIZATION_ID),
                        project_ids=[PROJECT_B],
                    ),
                ]
            ),
        ),
        secret: object = SimpleNamespace(
            success=True,
            data=SimpleNamespace(
                id=SECRET_A,
                key="db_password",
                organization_id=UUID(ORGANIZATION_ID),
                project_id=PROJECT_A,
                value=CANARY,
            ),
        ),
    ) -> None:
        self.auth_client = FakeAuth(login)
        self.projects_client = FakeProjects(projects)
        self.secrets_client = FakeSecrets(identifiers, secret)

    def auth(self) -> FakeAuth:
        return self.auth_client

    def projects(self) -> FakeProjects:
        return self.projects_client

    def secrets(self) -> FakeSecrets:
        return self.secrets_client


def response(data: object, *, success: bool = True) -> SimpleNamespace:
    return SimpleNamespace(success=success, data=SimpleNamespace(data=data))


def project(project_id: object, organization_id: object = UUID(ORGANIZATION_ID)) -> SimpleNamespace:
    return SimpleNamespace(id=project_id, organization_id=organization_id)


def identifier(
    secret_id: object,
    key: object,
    project_ids: object,
    organization_id: object = UUID(ORGANIZATION_ID),
) -> SimpleNamespace:
    return SimpleNamespace(
        id=secret_id,
        key=key,
        organization_id=organization_id,
        project_ids=project_ids,
    )


def reference(ref: str = "db_password", project_id: UUID | None = PROJECT_A) -> SecretReference:
    return SecretReference(
        ref=ref,
        project=None if project_id is None else str(project_id),
        locations=("apps.api.secrets",),
    )


class NativeCommandStub:
    def __init__(self, responses: Sequence[dict[str, object]]) -> None:
        self.responses = list(responses)

    def run_command(self, command: str) -> str:
        return json.dumps(self.responses.pop(0))


def real_sdk_client(responses: Sequence[dict[str, object]]) -> Any:
    sdk = pytest.importorskip("bitwarden_sdk")
    client = sdk.BitwardenClient.__new__(sdk.BitwardenClient)
    client.inner = NativeCommandStub(responses)
    return client


def sdk_success(data: dict[str, object]) -> dict[str, object]:
    return {"success": True, "data": data, "errorMessage": None}


def sdk_denial() -> dict[str, object]:
    return {"success": False, "data": None, "errorMessage": CANARY}


SDK_LOGIN_SUCCESS = sdk_success(
    {
        "authenticated": True,
        "forcePasswordReset": False,
        "resetMasterPassword": False,
        "twoFactor": None,
    }
)
SDK_PROJECTS_SUCCESS = sdk_success(
    {
        "data": [
            {
                "creationDate": "2026-01-01T00:00:00Z",
                "id": str(PROJECT_A),
                "name": "project-a",
                "organizationId": ORGANIZATION_ID,
                "revisionDate": "2026-01-01T00:00:00Z",
            }
        ]
    }
)
SDK_IDENTIFIERS_SUCCESS = sdk_success(
    {
        "data": [
            {
                "id": str(SECRET_A),
                "key": "db_password",
                "organizationId": ORGANIZATION_ID,
                "projectIds": [str(PROJECT_A)],
            }
        ]
    }
)


@pytest.mark.parametrize("factory", [None, _default_sdk_factory])
def test_test_only_config_requires_non_default_factory(factory: object) -> None:
    config = BwsConfig.for_test(
        access_token=FAKE_TOKEN,
        organization_id=ORGANIZATION_ID,
        api_url="http://localhost:8080",
        identity_url="http://localhost:8081",
    )

    with pytest.raises(BwsConfigurationError, match="factory"):
        BwsSecretResolver(config=config, sdk_factory=factory)  # type: ignore[arg-type]


def test_adapter_module_does_not_import_optional_sdk() -> None:
    script = """
import builtins
real_import = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "bitwarden_sdk":
        raise AssertionError("optional SDK imported at module import time")
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded
import infralink
import infralink.adapters.bws
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_injected_factory_logs_in_without_importing_optional_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()

    def reject_import(name: str) -> object:
        raise AssertionError(name)

    monkeypatch.setattr(importlib, "import_module", reject_import)
    resolver = BwsSecretResolver(
        config=BwsConfig.from_env(
            {
                "BWS_ACCESS_TOKEN": CANARY,
                "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
            }
        ),
        sdk_factory=lambda config: client,
    )

    assert client.auth_client.tokens == [CANARY]
    assert CANARY not in repr(resolver)
    assert CANARY not in str(resolver)


def test_default_factory_uses_sdk_21_settings_and_version_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    captured: list[dict[str, object]] = []

    class DeviceType:
        SDK = object()

    def bitwarden_client(settings: object) -> FakeClient:
        assert settings == {"settings": captured[-1]}
        return client

    def settings_factory(settings: dict[str, object]) -> dict[str, object]:
        captured.append(settings)
        return {"settings": settings}

    sdk = SimpleNamespace(
        BitwardenClient=bitwarden_client,
        DeviceType=DeviceType,
        client_settings_from_dict=settings_factory,
    )
    real_import_module = importlib.import_module
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: sdk if name == "bitwarden_sdk" else real_import_module(name),
    )

    BwsSecretResolver(
        config=BwsConfig.from_env(
            {
                "BWS_ACCESS_TOKEN": CANARY,
                "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
            }
        )
    )

    assert captured == [
        {
            "apiUrl": HOSTED_API_URL,
            "identityUrl": HOSTED_IDENTITY_URL,
            "deviceType": DeviceType.SDK,
            "userAgent": "infralink/0.2.0",
        }
    ]


def test_default_factory_normalizes_missing_optional_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import_module = importlib.import_module

    def missing_sdk(name: str) -> ModuleType:
        if name == "bitwarden_sdk":
            raise ModuleNotFoundError(CANARY)
        return real_import_module(name)

    monkeypatch.setattr(importlib, "import_module", missing_sdk)

    with pytest.raises(BwsProviderError) as caught:
        BwsSecretResolver(
            config=BwsConfig.from_env(
                {
                    "BWS_ACCESS_TOKEN": CANARY,
                    "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
                }
            )
        )

    assert caught.value.code is BwsErrorCode.PROVIDER_UNAVAILABLE
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    ("login", "code"),
    [
        (SimpleNamespace(success=False), BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED),
        (TimeoutError(CANARY), BwsErrorCode.PROVIDER_TIMEOUT),
        (RuntimeError(CANARY), BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED),
        (SimpleNamespace(), BwsErrorCode.PROVIDER_UNAVAILABLE),
        (SimpleNamespace(success="yes"), BwsErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
def test_login_failure_is_safe(login: object, code: BwsErrorCode) -> None:
    client = FakeClient(login=login)

    with pytest.raises(BwsProviderError) as caught:
        BwsSecretResolver(
            config=BwsConfig.from_env(
                {
                    "BWS_ACCESS_TOKEN": CANARY,
                    "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
                }
            ),
            sdk_factory=lambda config: client,
        )

    assert caught.value.code is code
    assert str(caught.value) == code.value
    assert CANARY not in str(caught.value)
    assert CANARY not in repr(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    ("login", "code"),
    [
        (
            SimpleNamespace(
                success=True,
                data=SimpleNamespace(authenticated=False),
            ),
            BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED,
        ),
        (
            SimpleNamespace(success=True, data=None),
            BwsErrorCode.PROVIDER_UNAVAILABLE,
        ),
        (
            SimpleNamespace(
                success=True,
                data=SimpleNamespace(authenticated="yes"),
            ),
            BwsErrorCode.PROVIDER_UNAVAILABLE,
        ),
    ],
)
def test_login_validates_authenticated_state(login: object, code: BwsErrorCode) -> None:
    client = FakeClient(login=login)

    with pytest.raises(BwsProviderError) as caught:
        BwsSecretResolver(
            config=BwsConfig.from_env(
                {
                    "BWS_ACCESS_TOKEN": CANARY,
                    "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
                }
            ),
            sdk_factory=lambda config: client,
        )

    assert caught.value.code is code


@pytest.mark.parametrize(
    ("responses", "operation", "code"),
    [
        ([sdk_denial()], "construct", BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED),
        (
            [SDK_LOGIN_SUCCESS, sdk_denial()],
            "audit",
            BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        ),
        (
            [SDK_LOGIN_SUCCESS, SDK_PROJECTS_SUCCESS, sdk_denial()],
            "audit",
            BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        ),
        (
            [
                SDK_LOGIN_SUCCESS,
                SDK_PROJECTS_SUCCESS,
                SDK_IDENTIFIERS_SUCCESS,
                sdk_denial(),
            ],
            "resolve",
            BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        ),
    ],
)
def test_real_sdk_generic_denial_is_classified_by_operation_stage(
    responses: list[dict[str, object]], operation: str, code: BwsErrorCode
) -> None:
    client = real_sdk_client(responses)
    config = BwsConfig.from_env(
        {
            "BWS_ACCESS_TOKEN": CANARY,
            "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
        }
    )

    with pytest.raises(BwsProviderError) as caught:
        resolver = BwsSecretResolver(
            config=config,
            sdk_factory=lambda adapter_config: client,
        )
        if operation == "audit":
            resolver.audit([reference()])
        elif operation == "resolve":
            resolver.resolve(reference())

    assert caught.value.code is code
    assert CANARY not in repr(caught.value)
    assert caught.value.__context__ is None


def test_provider_error_is_safely_copyable_and_picklable() -> None:
    error = BwsProviderError(BwsErrorCode.PROVIDER_TIMEOUT)

    assert copy.copy(error).code is BwsErrorCode.PROVIDER_TIMEOUT
    assert pickle.loads(pickle.dumps(error)).code is BwsErrorCode.PROVIDER_TIMEOUT


def make_resolver(client: FakeClient) -> BwsSecretResolver:
    return BwsSecretResolver(
        config=BwsConfig.from_env(
            {
                "BWS_ACCESS_TOKEN": CANARY,
                "BWS_ORGANIZATION_ID": ORGANIZATION_ID,
            }
        ),
        sdk_factory=lambda config: client,
    )


def test_audit_reports_accessible_match_without_getting_value() -> None:
    client = FakeClient()
    resolver = make_resolver(client)

    result = resolver.audit([reference()])

    assert result == [
        SecretAudit(
            ref="db_password",
            project=str(PROJECT_A),
            present=True,
            accessible=True,
        )
    ]
    assert client.projects_client.organizations == [ORGANIZATION_ID]
    assert client.secrets_client.organizations == [ORGANIZATION_ID]
    assert client.secrets_client.get_calls == []


def test_declared_project_uuid_is_canonicalized_in_audit_result() -> None:
    project_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    client = FakeClient(
        projects=response([project(project_id)]),
        identifiers=response([identifier(SECRET_A, "db_password", [project_id])]),
    )
    declared = SecretReference(
        ref="db_password",
        project=str(project_id).upper(),
        locations=("apps.api.secrets",),
    )

    result = make_resolver(client).audit([declared])

    assert result[0].project == str(project_id)


def test_invalid_declared_project_fails_before_provider_listing() -> None:
    client = FakeClient()
    declared = SecretReference(
        ref="db_password",
        project="not-a-uuid",
        locations=("apps.api.secrets",),
    )

    with pytest.raises(BwsConfigurationError, match="project"):
        make_resolver(client).audit([declared])

    assert client.projects_client.organizations == []


@pytest.mark.parametrize(
    ("stage", "payload"),
    [
        ("projects", response([project(PROJECT_A, UUID(MIXED_ORGANIZATION_ID))])),
        (
            "identifiers",
            response(
                [
                    identifier(
                        SECRET_A,
                        "db_password",
                        [PROJECT_A],
                        UUID(MIXED_ORGANIZATION_ID),
                    )
                ]
            ),
        ),
    ],
)
def test_provider_metadata_must_match_configured_organization(stage: str, payload: object) -> None:
    client = FakeClient(
        projects=payload if stage == "projects" else response([project(PROJECT_A)]),
        identifiers=payload
        if stage == "identifiers"
        else response([identifier(SECRET_A, "db_password", [PROJECT_A])]),
    )

    with pytest.raises(BwsProviderError) as caught:
        make_resolver(client).audit([reference()])

    assert caught.value.code is BwsErrorCode.PROVIDER_UNAVAILABLE


def test_project_metadata_requires_organization_identity() -> None:
    client = FakeClient(
        projects=response([SimpleNamespace(id=PROJECT_A)]),
        identifiers=response([identifier(SECRET_A, "db_password", [PROJECT_A])]),
    )

    with pytest.raises(BwsProviderError) as caught:
        make_resolver(client).audit([reference()])

    assert caught.value.code is BwsErrorCode.PROVIDER_UNAVAILABLE
    assert client.secrets_client.organizations == []


class GuardedSecretPayload:
    def __init__(
        self,
        *,
        secret_id: object = SECRET_A,
        key: object = "db_password",
        organization_id: object = UUID(ORGANIZATION_ID),
        project_id: object = PROJECT_A,
    ) -> None:
        self.id = secret_id
        self.key = key
        self.organization_id = organization_id
        self.project_id = project_id
        self.value_reads = 0

    @property
    def value(self) -> str:
        self.value_reads += 1
        return CANARY


@pytest.mark.parametrize(
    "payload",
    [
        GuardedSecretPayload(secret_id=SECRET_B),
        GuardedSecretPayload(key="stale_key"),
        GuardedSecretPayload(organization_id=UUID(MIXED_ORGANIZATION_ID)),
        GuardedSecretPayload(project_id=PROJECT_B),
        GuardedSecretPayload(secret_id="not-a-uuid"),
    ],
)
def test_resolve_rejects_stale_or_mismatched_get_identity_before_value(
    payload: GuardedSecretPayload,
) -> None:
    client = FakeClient(secret=SimpleNamespace(success=True, data=payload))

    with pytest.raises(BwsProviderError) as caught:
        make_resolver(client).resolve(reference())

    assert caught.value.code is BwsErrorCode.PROVIDER_UNAVAILABLE
    assert payload.value_reads == 0


def test_sdk_string_ids_are_normalized_without_leaking_secret_id() -> None:
    client = FakeClient(
        projects=response(
            [
                SimpleNamespace(
                    id=str(PROJECT_A),
                    organization_id=ORGANIZATION_ID,
                )
            ]
        ),
        identifiers=response(
            [
                identifier(
                    str(SECRET_A),
                    "db_password",
                    [str(PROJECT_A)],
                )
            ]
        ),
    )

    result = make_resolver(client).audit([reference()])

    assert result[0].present is True
    assert str(SECRET_A) not in repr(result)


def test_audit_reports_partial_project_visibility_per_reference() -> None:
    client = FakeClient(
        projects=response([project(PROJECT_A)]),
        identifiers=response([identifier(SECRET_A, "db_password", [PROJECT_A])]),
    )

    result = make_resolver(client).audit([reference(), reference("api_key", PROJECT_B)])

    assert result == [
        SecretAudit(
            ref="db_password",
            project=str(PROJECT_A),
            present=True,
            accessible=True,
        ),
        SecretAudit(
            ref="api_key",
            project=str(PROJECT_B),
            present=None,
            accessible=False,
            error_code="project_unavailable",
        ),
    ]
    assert client.secrets_client.get_calls == []


def test_audit_reports_absent_identifier_honestly() -> None:
    client = FakeClient(identifiers=response([]))

    result = make_resolver(client).audit([reference()])

    assert result == [
        SecretAudit(
            ref="db_password",
            project=str(PROJECT_A),
            present=None,
            accessible=False,
            error_code="unavailable_or_missing",
        )
    ]


def test_zero_accessible_configured_projects_is_provider_wide_failure() -> None:
    client = FakeClient(projects=response([project(PROJECT_B)]))

    with pytest.raises(BwsProviderError) as caught:
        make_resolver(client).audit([reference()])

    assert caught.value.code is BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED
    assert client.secrets_client.organizations == []


def test_unscoped_reference_fails_without_listing_organization() -> None:
    client = FakeClient()

    with pytest.raises(BwsConfigurationError, match="project"):
        make_resolver(client).audit([reference(project_id=None)])

    assert client.projects_client.organizations == []
    assert client.secrets_client.organizations == []


def test_empty_audit_does_not_list_provider_metadata() -> None:
    client = FakeClient()

    assert make_resolver(client).audit([]) == []
    assert client.projects_client.organizations == []
    assert client.secrets_client.organizations == []


def test_duplicate_identifier_key_and_project_fails_closed() -> None:
    client = FakeClient(
        identifiers=response(
            [
                identifier(SECRET_A, "db_password", [PROJECT_A]),
                identifier(SECRET_B, "db_password", [PROJECT_A]),
            ]
        )
    )

    with pytest.raises(BwsProviderError) as caught:
        make_resolver(client).audit([reference()])

    assert caught.value.code is BwsErrorCode.PROVIDER_UNAVAILABLE
    assert str(SECRET_A) not in repr(caught.value)
    assert str(SECRET_B) not in repr(caught.value)


@pytest.mark.parametrize(
    ("stage", "failure", "code"),
    [
        ("projects", TimeoutError(CANARY), BwsErrorCode.PROVIDER_TIMEOUT),
        ("projects", RuntimeError(CANARY), BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED),
        ("projects", SimpleNamespace(success=False), BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED),
        ("identifiers", TimeoutError(CANARY), BwsErrorCode.PROVIDER_TIMEOUT),
        ("identifiers", RuntimeError(CANARY), BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED),
        (
            "identifiers",
            SimpleNamespace(success=False),
            BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        ),
    ],
)
def test_audit_provider_failures_are_normalized(
    stage: str, failure: object, code: BwsErrorCode
) -> None:
    client = FakeClient(
        projects=failure if stage == "projects" else response([project(PROJECT_A)]),
        identifiers=failure if stage == "identifiers" else response([]),
    )

    with pytest.raises(BwsProviderError) as caught:
        make_resolver(client).audit([reference()])

    assert caught.value.code is code
    assert CANARY not in repr(caught.value)
    assert CANARY not in str(caught.value)


@pytest.mark.parametrize(
    ("stage", "malformed"),
    [
        ("projects", SimpleNamespace()),
        ("projects", SimpleNamespace(success="yes")),
        ("projects", SimpleNamespace(success=True, data=None)),
        ("projects", response("not-a-list")),
        ("projects", response([SimpleNamespace()])),
        ("projects", response([SimpleNamespace(id=object())])),
        ("projects", response([project(PROJECT_A), project(PROJECT_A)])),
        ("identifiers", SimpleNamespace()),
        ("identifiers", SimpleNamespace(success=True, data=None)),
        ("identifiers", response("not-a-list")),
        ("identifiers", response([SimpleNamespace()])),
        ("identifiers", response([identifier(SECRET_A, 7, [PROJECT_A])])),
        ("identifiers", response([identifier(SECRET_A, "db_password", "bad")])),
        ("identifiers", response([identifier(object(), "db_password", [PROJECT_A])])),
        ("identifiers", response([identifier(SECRET_A, "db_password", [object()])])),
    ],
)
def test_malformed_audit_response_fails_provider_unavailable(stage: str, malformed: object) -> None:
    client = FakeClient(
        projects=malformed if stage == "projects" else response([project(PROJECT_A)]),
        identifiers=malformed if stage == "identifiers" else response([]),
    )

    with pytest.raises(BwsProviderError) as caught:
        make_resolver(client).audit([reference()])

    assert caught.value.code is BwsErrorCode.PROVIDER_UNAVAILABLE


def test_loggable_audit_and_adapter_objects_do_not_contain_sensitive_data() -> None:
    client = FakeClient(identifiers=response([identifier(SECRET_A, "db_password", [PROJECT_A])]))
    resolver = make_resolver(client)

    audit = resolver.audit([reference()])
    loggable = repr((resolver, resolver._config, audit))

    assert CANARY not in loggable
    assert str(SECRET_A) not in loggable
    assert not hasattr(resolver, "create")
    assert not hasattr(resolver, "update")
    assert not hasattr(resolver, "delete")


def test_resolve_lists_declared_metadata_then_gets_exactly_one_opaque_value() -> None:
    client = FakeClient()

    value = make_resolver(client).resolve(reference())

    assert isinstance(value, SecretValue)
    assert str(value) == "[REDACTED]"
    assert CANARY not in repr(value)
    assert value.reveal() == CANARY
    assert client.projects_client.organizations == [ORGANIZATION_ID]
    assert client.secrets_client.organizations == [ORGANIZATION_ID]
    assert client.secrets_client.get_calls == [str(SECRET_A)]


def test_resolve_unscoped_reference_does_not_search_organization() -> None:
    client = FakeClient()

    with pytest.raises(BwsConfigurationError, match="project"):
        make_resolver(client).resolve(reference(project_id=None))

    assert client.projects_client.organizations == []
    assert client.secrets_client.organizations == []
    assert client.secrets_client.get_calls == []


@pytest.mark.parametrize(
    ("client", "code"),
    [
        (
            FakeClient(projects=response([])),
            BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        ),
        (
            FakeClient(identifiers=response([])),
            BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        ),
        (
            FakeClient(
                identifiers=response(
                    [
                        identifier(SECRET_A, "db_password", [PROJECT_A]),
                        identifier(SECRET_B, "db_password", [PROJECT_A]),
                    ]
                )
            ),
            BwsErrorCode.PROVIDER_UNAVAILABLE,
        ),
    ],
)
def test_resolve_fails_closed_before_get(client: FakeClient, code: BwsErrorCode) -> None:
    with pytest.raises(BwsProviderError) as caught:
        make_resolver(client).resolve(reference())

    assert caught.value.code is code
    assert client.secrets_client.get_calls == []


@pytest.mark.parametrize(
    ("get_response", "code"),
    [
        (TimeoutError(CANARY), BwsErrorCode.PROVIDER_TIMEOUT),
        (RuntimeError(CANARY), BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED),
        (SimpleNamespace(success=False), BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED),
        (SimpleNamespace(), BwsErrorCode.PROVIDER_UNAVAILABLE),
        (SimpleNamespace(success="yes"), BwsErrorCode.PROVIDER_UNAVAILABLE),
        (SimpleNamespace(success=True, data=None), BwsErrorCode.PROVIDER_UNAVAILABLE),
        (
            SimpleNamespace(
                success=True,
                data=SimpleNamespace(
                    id=SECRET_A,
                    key="db_password",
                    organization_id=UUID(ORGANIZATION_ID),
                    project_id=PROJECT_A,
                    value=object(),
                ),
            ),
            BwsErrorCode.PROVIDER_UNAVAILABLE,
        ),
    ],
)
def test_resolve_normalizes_get_failures(get_response: object, code: BwsErrorCode) -> None:
    client = FakeClient(secret=get_response)

    with pytest.raises(BwsProviderError) as caught:
        make_resolver(client).resolve(reference())

    assert caught.value.code is code
    assert CANARY not in repr(caught.value)
    assert CANARY not in str(caught.value)
    assert client.secrets_client.get_calls == [str(SECRET_A)]
