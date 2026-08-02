"""Health check implementations for infrastructure edges."""

from __future__ import annotations

import errno
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from infralink.core.edges import Edge
from infralink.core.resolver import EdgeResolver, ResolutionError


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    edge_id: str
    edge_type: str
    target_endpoint: str
    healthy: bool
    latency_ms: float | None
    message: str | None
    criticality: str
    check_type: str
    timestamp: float
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "edge_id": self.edge_id,
            "edge_type": self.edge_type,
            "target_endpoint": self.target_endpoint,
            "healthy": self.healthy,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "criticality": self.criticality,
            "check_type": self.check_type,
            "timestamp": self.timestamp,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class _ProbeOutcome:
    healthy: bool
    latency_ms: float | None
    message: str | None
    error_code: str | None

    def legacy(self) -> tuple[bool, float | None, str | None]:
        return self.healthy, self.latency_ms, self.message


def normalize_health_result(result: HealthCheckResult) -> tuple[str, str | None]:
    """Return stable health state without exposing provider or endpoint details."""
    if result.healthy:
        return "healthy", None
    if result.error_code is not None:
        unavailable = {
            "connection_refused",
            "network_unreachable",
            "resolution_failed",
            "timeout",
        }
        if result.error_code in unavailable:
            return "unavailable", result.error_code
        if result.error_code in {"check_failed", "http_error", "network_error"}:
            return "unhealthy", result.error_code
        return "unhealthy", "check_failed"
    if result.check_type == "resolution":
        return "unavailable", "resolution_failed"
    message = (result.message or "").casefold()
    if message == "connection timed out":
        return "unavailable", "timeout"
    if message == "connection refused":
        return "unavailable", "connection_refused"
    if message == "network unreachable":
        return "unavailable", "network_unreachable"
    if message == "network error":
        return "unhealthy", "network_error"
    return "unhealthy", "check_failed"


def _transport_error_code(error: BaseException | int) -> str:
    if isinstance(error, int):
        if error == errno.ECONNREFUSED:
            return "connection_refused"
        if error == errno.ETIMEDOUT:
            return "timeout"
        if error in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
            return "network_unreachable"
        return "network_error"

    pending = [error]
    seen: set[int] = set()
    fallback = "check_failed"
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, TimeoutError):
            return "timeout"
        if isinstance(current, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(current, OSError):
            fallback = "network_error"
            if current.errno == errno.ETIMEDOUT:
                return "timeout"
            if current.errno == errno.ECONNREFUSED:
                return "connection_refused"
            if current.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
                return "network_unreachable"
        for nested in (
            getattr(current, "reason", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
            elif isinstance(nested, int):
                return _transport_error_code(nested)
    return fallback


def _failure(error_code: str, latency_ms: float | None = None) -> _ProbeOutcome:
    messages = {
        "check_failed": "Health check failed",
        "connection_refused": "Connection refused",
        "http_error": "HTTP request failed",
        "network_error": "Network error",
        "network_unreachable": "Network unreachable",
        "timeout": "Connection timed out",
    }
    return _ProbeOutcome(
        healthy=False,
        latency_ms=latency_ms,
        message=messages.get(error_code, "Health check failed"),
        error_code=error_code,
    )


def _check_tcp_outcome(host: str, port: int, timeout: int) -> _ProbeOutcome:
    try:
        start = time.monotonic()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        latency = (time.monotonic() - start) * 1000
        sock.close()
        if result == 0:
            return _ProbeOutcome(True, latency, None, None)
        return _failure(_transport_error_code(result), latency)
    except Exception as exc:
        return _failure(_transport_error_code(exc))


def check_tcp(host: str, port: int, timeout: int = 5) -> tuple[bool, float | None, str | None]:
    """
    Perform TCP connectivity check.

    Returns (healthy, latency_ms, error_message).
    """
    return _check_tcp_outcome(host, port, timeout).legacy()


def _check_http_outcome(
    host: str,
    port: int,
    path: str,
    timeout: int,
    https: bool,
) -> _ProbeOutcome:
    protocol = "https" if https else "http"
    url = f"{protocol}://{host}:{port}{path}"
    try:
        start = time.monotonic()
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            latency = (time.monotonic() - start) * 1000
            if 200 <= response.status < 400:
                return _ProbeOutcome(True, latency, None, None)
            return _failure("http_error", latency)
    except urllib.error.HTTPError:
        return _failure("http_error")
    except Exception as exc:
        return _failure(_transport_error_code(exc))


def check_http(
    host: str,
    port: int,
    path: str = "/",
    timeout: int = 5,
    https: bool = False,
) -> tuple[bool, float | None, str | None]:
    """
    Perform HTTP health check.

    Returns (healthy, latency_ms, error_message).
    """
    return _check_http_outcome(host, port, path, timeout, https).legacy()


def _check_redis_outcome(host: str, port: int, timeout: int) -> _ProbeOutcome:
    try:
        start = time.monotonic()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(b"*1\r\n$4\r\nPING\r\n")
        response = sock.recv(1024)
        latency = (time.monotonic() - start) * 1000
        sock.close()
        if b"+PONG" in response:
            return _ProbeOutcome(True, latency, None, None)
        if b"-NOAUTH" in response:
            return _ProbeOutcome(True, latency, "Auth required", None)
        return _failure("check_failed", latency)
    except Exception as exc:
        return _failure(_transport_error_code(exc))


def check_redis_ping(
    host: str, port: int, timeout: int = 5
) -> tuple[bool, float | None, str | None]:
    """
    Perform Redis PING check.

    Returns (healthy, latency_ms, error_message).
    """
    return _check_redis_outcome(host, port, timeout).legacy()


def check_edge_health(
    edge: Edge,
    resolver: EdgeResolver,
    timeout: int = 5,
) -> HealthCheckResult:
    """
    Perform health check for an edge.

    Automatically selects appropriate check based on edge type and configuration.
    """
    timestamp = time.time()

    try:
        target_ip = resolver.get_target_ip(edge.id)
        target_port = resolver.get_target_port(edge.id)
        target_endpoint = f"{target_ip}:{target_port}"
    except ResolutionError as e:
        return HealthCheckResult(
            edge_id=edge.id,
            edge_type=edge.type.value,
            target_endpoint="unknown",
            healthy=False,
            latency_ms=None,
            message=str(e),
            criticality=edge.criticality.value,
            check_type="resolution",
            timestamp=timestamp,
            error_code="resolution_failed",
        )

    # Determine check type
    check_config = edge.healthcheck
    check_type = check_config.type.value

    # Perform appropriate check
    if check_type == "tcp":
        outcome = _check_tcp_outcome(target_ip, target_port, timeout)
    elif check_type in ("http", "https"):
        path = check_config.path or "/"
        outcome = _check_http_outcome(
            target_ip, target_port, path, timeout, https=(check_type == "https")
        )
    elif check_type == "ping":
        outcome = _check_redis_outcome(target_ip, target_port, timeout)
    elif check_type == "api":
        path = check_config.path or "/health"
        outcome = _check_http_outcome(target_ip, target_port, path, timeout, False)
    else:
        outcome = _check_tcp_outcome(target_ip, target_port, timeout)
        check_type = "tcp"

    return HealthCheckResult(
        edge_id=edge.id,
        edge_type=edge.type.value,
        target_endpoint=target_endpoint,
        healthy=outcome.healthy,
        latency_ms=outcome.latency_ms,
        message=outcome.message,
        criticality=edge.criticality.value,
        check_type=check_type,
        timestamp=timestamp,
        error_code=outcome.error_code,
    )


def check_all_edges(
    resolver: EdgeResolver,
    timeout: int = 5,
    critical_only: bool = False,
) -> list[HealthCheckResult]:
    """
    Check health of all edges.

    Returns list of HealthCheckResult.
    """
    edges = list(resolver._edges.critical_edges()) if critical_only else list(resolver._edges)

    results = []
    for edge in edges:
        result = check_edge_health(edge, resolver, timeout)
        results.append(result)

    return results
