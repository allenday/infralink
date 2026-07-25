import errno
import urllib.error

import pytest

from infralink.core.edges import Edge
from infralink.core.resolver import ResolutionError
from infralink.health.checks import (
    HealthCheckResult,
    check_edge_health,
    normalize_health_result,
)


class _Socket:
    def __init__(self, result: int) -> None:
        self.result = result

    def settimeout(self, timeout: int) -> None:
        pass

    def connect_ex(self, endpoint: tuple[str, int]) -> int:
        return self.result

    def close(self) -> None:
        pass


class _Resolver:
    def get_target_ip(self, edge_id: str) -> str:
        return "192.0.2.1"

    def get_target_port(self, edge_id: str) -> int:
        return 5432


class _MissingPortResolver(_Resolver):
    def get_target_port(self, edge_id: str) -> int:
        raise ResolutionError(f"No target port declared for edge {edge_id}")


def _edge(check_type: str = "tcp") -> Edge:
    return Edge(
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "type": "database",
            "from": {"hosts": "*"},
            "to": {
                "host": "00000000-0000-4000-8000-000000000002",
                "service": "postgresql",
                "port": 5432,
            },
            "healthcheck": {"type": check_type},
        }
    )


def test_missing_target_port_is_a_stable_resolution_failure() -> None:
    result = check_edge_health(_edge(), _MissingPortResolver())  # type: ignore[arg-type]

    assert result.healthy is False
    assert result.check_type == "resolution"
    assert result.error_code == "resolution_failed"
    assert result.message == (
        "No target port declared for edge 00000000-0000-4000-8000-000000000001"
    )
    assert result.target_endpoint == "unknown"


@pytest.mark.parametrize(
    ("socket_errno", "expected_code"),
    [
        (errno.ECONNREFUSED, "connection_refused"),
        (errno.ETIMEDOUT, "timeout"),
        (errno.ENETUNREACH, "network_unreachable"),
        (errno.EHOSTUNREACH, "network_unreachable"),
        (errno.EACCES, "network_error"),
    ],
)
def test_tcp_errno_maps_to_stable_error_code_without_os_prose(
    monkeypatch: pytest.MonkeyPatch,
    socket_errno: int,
    expected_code: str,
) -> None:
    monkeypatch.setattr(
        "infralink.health.checks.socket.socket",
        lambda *args, **kwargs: _Socket(socket_errno),
    )

    result = check_edge_health(_edge(), _Resolver(), timeout=2)  # type: ignore[arg-type]

    assert result.healthy is False
    assert normalize_health_result(result) == (
        "unavailable" if expected_code != "network_error" else "unhealthy",
        expected_code,
    )
    assert str(socket_errno) not in (result.message or "")


def test_typed_error_code_wins_over_misleading_provider_message() -> None:
    result = HealthCheckResult(
        edge_id="edge-1",
        edge_type="database",
        target_endpoint="unknown",
        healthy=False,
        latency_ms=None,
        message="provider said connection refused but returned an unknown outcome",
        criticality="medium",
        check_type="tcp",
        timestamp=1.0,
        error_code="network_error",
    )

    assert normalize_health_result(result) == ("unhealthy", "network_error")


def test_unknown_typed_error_code_is_normalized_to_check_failed() -> None:
    result = HealthCheckResult(
        edge_id="edge-1",
        edge_type="database",
        target_endpoint="unknown",
        healthy=False,
        latency_ms=None,
        message=None,
        criticality="medium",
        check_type="tcp",
        timestamp=1.0,
        error_code="provider-canary",
    )

    assert normalize_health_result(result) == ("unhealthy", "check_failed")


class _RedisSocket:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def settimeout(self, timeout: int) -> None:
        pass

    def connect(self, endpoint: tuple[str, int]) -> None:
        raise self.error


def test_http_url_timeout_has_typed_code_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "timeout-provider-canary"

    def timeout(*args, **kwargs):
        raise urllib.error.URLError(TimeoutError(canary))

    monkeypatch.setattr("urllib.request.urlopen", timeout)

    result = check_edge_health(_edge("http"), _Resolver(), timeout=2)  # type: ignore[arg-type]

    assert result.error_code == "timeout"
    assert normalize_health_result(result) == ("unavailable", "timeout")
    assert canary not in (result.message or "")


def test_http_url_errno_reason_has_typed_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refused(*args, **kwargs):
        raise urllib.error.URLError(errno.ECONNREFUSED)

    monkeypatch.setattr("urllib.request.urlopen", refused)

    result = check_edge_health(_edge("http"), _Resolver(), timeout=2)  # type: ignore[arg-type]

    assert result.error_code == "connection_refused"


def test_redis_connection_refused_has_typed_code_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "redis-provider-canary"
    monkeypatch.setattr(
        "infralink.health.checks.socket.socket",
        lambda *args, **kwargs: _RedisSocket(ConnectionRefusedError(canary)),
    )

    result = check_edge_health(_edge("ping"), _Resolver(), timeout=2)  # type: ignore[arg-type]

    assert result.error_code == "connection_refused"
    assert normalize_health_result(result) == ("unavailable", "connection_refused")
    assert canary not in (result.message or "")


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (RuntimeError("library-provider-canary"), "network_unreachable"),
        (ValueError("fallback-provider-canary"), "check_failed"),
    ],
)
def test_redis_wrapped_cause_and_fallback_have_stable_codes(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected_code: str,
) -> None:
    if isinstance(error, RuntimeError):
        error.__cause__ = OSError(errno.ENETUNREACH, "os-provider-canary")
    monkeypatch.setattr(
        "infralink.health.checks.socket.socket",
        lambda *args, **kwargs: _RedisSocket(error),
    )

    result = check_edge_health(_edge("ping"), _Resolver(), timeout=2)  # type: ignore[arg-type]

    assert result.error_code == expected_code
    assert "canary" not in (result.message or "")


class _HttpResponse:
    status = 503

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def test_http_non_success_status_is_stable_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _HttpResponse(),
    )

    result = check_edge_health(_edge("http"), _Resolver(), timeout=2)  # type: ignore[arg-type]

    assert result.error_code == "http_error"
    assert result.message == "HTTP request failed"
    assert normalize_health_result(result) == ("unhealthy", "http_error")
