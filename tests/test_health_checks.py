import errno

import pytest

from infralink.core.edges import Edge
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


def _edge() -> Edge:
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
            "healthcheck": {"type": "tcp"},
        }
    )


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
