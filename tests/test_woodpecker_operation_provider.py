from __future__ import annotations

import json

import pytest

from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.operations import ApplyRequest, WoodpeckerOperationProvider


def _request() -> ApplyRequest:
    return ApplyRequest(
        host_uuid="32a3324f-c3d0-4a4f-9587-52c099bcb3fb",
        registry_revision="a" * 40,
        selector="release-channels/v2.yml",
    )


def test_submit_creates_one_manual_woodpecker_pipeline_with_opaque_durable_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.cli import operations

    captured: dict[str, object] = {}

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"number":42,"status":"pending"}'

    def fake_urlopen(request: object, *, timeout: int) -> Response:
        captured["url"] = request.full_url  # type: ignore[attr-defined]
        captured["method"] = request.method  # type: ignore[attr-defined]
        captured["headers"] = dict(request.header_items())  # type: ignore[attr-defined]
        captured["body"] = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(operations, "urlopen", fake_urlopen)
    provider = WoodpeckerOperationProvider(
        url="https://woodpecker.example.test/",
        repository_id=30,
        branch="main",
        token="token-value",
    )

    record = provider.submit(_request())

    assert record.id == "woodpecker/30/42"
    assert record.state == "queued"
    assert captured == {
        "url": "https://woodpecker.example.test/api/repos/30/pipelines",
        "method": "POST",
        "headers": {
            "Accept": "application/json",
            "Content-type": "application/json",
            "Authorization": "Bearer token-value",
        },
        "body": {
            "branch": "main",
            "variables": {
                "INFRALINK_APPLY_HOST_UUID": "32a3324f-c3d0-4a4f-9587-52c099bcb3fb",
                "INFRALINK_APPLY_REGISTRY_REVISION": "a" * 40,
                "INFRALINK_APPLY_SELECTOR": "release-channels/v2.yml",
            },
        },
        "timeout": 15,
    }


@pytest.mark.parametrize(
    ("woodpecker_status", "expected_state"),
    [
        ("pending", "queued"),
        ("running", "applying"),
        ("blocked", "applying"),
        ("success", "converged"),
        ("failure", "failed"),
        ("error", "failed"),
        ("killed", "failed"),
    ],
)
def test_status_maps_woodpecker_pipeline_state(
    monkeypatch: pytest.MonkeyPatch, woodpecker_status: str, expected_state: str
) -> None:
    from infralink.cli import operations

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"number": 42, "status": woodpecker_status}).encode()

    captured_url: list[str] = []

    def fake_urlopen(request: object, *, timeout: int) -> Response:
        captured_url.append(request.full_url)  # type: ignore[attr-defined]
        assert timeout == 15
        return Response()

    monkeypatch.setattr(operations, "urlopen", fake_urlopen)
    provider = WoodpeckerOperationProvider(
        url="https://woodpecker.example.test",
        repository_id=30,
        branch="main",
        token="token-value",
    )

    record = provider.status("woodpecker/30/42")

    assert record.id == "woodpecker/30/42"
    assert record.state == expected_state
    assert captured_url == ["https://woodpecker.example.test/api/repos/30/pipelines/42"]


def test_status_rejects_a_pipeline_from_another_repository() -> None:
    provider = WoodpeckerOperationProvider(
        url="https://woodpecker.example.test",
        repository_id=30,
        branch="main",
        token="token-value",
    )

    with pytest.raises(CliFailure) as raised:
        provider.status("woodpecker/31/42")

    assert raised.value.code == ErrorCode.USAGE_ERROR


def test_unknown_woodpecker_state_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from infralink.cli import operations

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"number":42,"status":"new-unknown-state"}'

    monkeypatch.setattr(operations, "urlopen", lambda *_args, **_kwargs: Response())
    provider = WoodpeckerOperationProvider(
        url="https://woodpecker.example.test",
        repository_id=30,
        branch="main",
        token="token-value",
    )

    with pytest.raises(CliFailure) as raised:
        provider.status("woodpecker/30/42")

    assert raised.value.code == ErrorCode.PROVIDER_UNAVAILABLE


def test_environment_configuration_resolves_the_named_token_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.cli.operations import operation_provider_from_environment

    monkeypatch.setenv("INFRALINK_WOODPECKER_URL", "https://woodpecker.example.test")
    monkeypatch.setenv("INFRALINK_WOODPECKER_REPOSITORY_ID", "30")
    monkeypatch.setenv("INFRALINK_WOODPECKER_BRANCH", "main")
    monkeypatch.setenv("INFRALINK_WOODPECKER_TOKEN_ENV", "CONTROL_HOST_WOODPECKER_TOKEN")
    monkeypatch.setenv("CONTROL_HOST_WOODPECKER_TOKEN", "token-value")

    provider = operation_provider_from_environment()

    assert isinstance(provider, WoodpeckerOperationProvider)


def test_environment_configuration_requires_all_control_host_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.cli.operations import operation_provider_from_environment

    for name in (
        "INFRALINK_WOODPECKER_URL",
        "INFRALINK_WOODPECKER_REPOSITORY_ID",
        "INFRALINK_WOODPECKER_BRANCH",
        "INFRALINK_WOODPECKER_TOKEN_ENV",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(CliFailure) as raised:
        operation_provider_from_environment()

    assert raised.value.code == ErrorCode.CONFIGURATION_REQUIRED
    assert raised.value.details["missing_environment"] == [
        "INFRALINK_WOODPECKER_BRANCH",
        "INFRALINK_WOODPECKER_REPOSITORY_ID",
        "INFRALINK_WOODPECKER_TOKEN_ENV",
        "INFRALINK_WOODPECKER_URL",
    ]
