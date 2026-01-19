"""Health check implementations for infrastructure edges."""

from __future__ import annotations

import socket
import time
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
        }


def check_tcp(host: str, port: int, timeout: int = 5) -> tuple[bool, float | None, str | None]:
    """
    Perform TCP connectivity check.

    Returns (healthy, latency_ms, error_message).
    """
    try:
        start = time.monotonic()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        latency = (time.monotonic() - start) * 1000
        sock.close()

        if result == 0:
            return True, latency, None
        return False, latency, f"Connection refused (code: {result})"
    except socket.timeout:
        return False, None, "Connection timed out"
    except socket.gaierror as e:
        return False, None, f"DNS resolution failed: {e}"
    except OSError as e:
        return False, None, f"Network error: {e}"


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
    import urllib.request
    import urllib.error

    protocol = "https" if https else "http"
    url = f"{protocol}://{host}:{port}{path}"

    try:
        start = time.monotonic()
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            latency = (time.monotonic() - start) * 1000
            if 200 <= response.status < 400:
                return True, latency, None
            return False, latency, f"HTTP {response.status}"
    except urllib.error.HTTPError as e:
        return False, None, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, None, f"URL error: {e.reason}"
    except Exception as e:
        return False, None, f"Request failed: {e}"


def check_redis_ping(host: str, port: int, timeout: int = 5) -> tuple[bool, float | None, str | None]:
    """
    Perform Redis PING check.

    Returns (healthy, latency_ms, error_message).
    """
    try:
        start = time.monotonic()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))

        # Send PING command
        sock.sendall(b"*1\r\n$4\r\nPING\r\n")

        # Read response
        response = sock.recv(1024)
        latency = (time.monotonic() - start) * 1000
        sock.close()

        if b"+PONG" in response:
            return True, latency, None
        if b"-NOAUTH" in response:
            # Auth required but service is responding
            return True, latency, "Auth required"
        return False, latency, f"Unexpected response: {response[:50]}"
    except Exception as e:
        return False, None, f"Redis check failed: {e}"


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
        target_port = edge.target_port
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
        )

    # Determine check type
    check_config = edge.healthcheck
    check_type = check_config.type.value

    # Perform appropriate check
    if check_type == "tcp":
        healthy, latency, message = check_tcp(target_ip, target_port, timeout)
    elif check_type in ("http", "https"):
        path = check_config.path or "/"
        healthy, latency, message = check_http(
            target_ip, target_port, path, timeout, https=(check_type == "https")
        )
    elif check_type == "ping":
        # Redis-style ping
        healthy, latency, message = check_redis_ping(target_ip, target_port, timeout)
    elif check_type == "api":
        # Generic API check (HTTP GET)
        path = check_config.path or "/health"
        healthy, latency, message = check_http(target_ip, target_port, path, timeout)
    else:
        # Default to TCP check
        healthy, latency, message = check_tcp(target_ip, target_port, timeout)
        check_type = "tcp"

    return HealthCheckResult(
        edge_id=edge.id,
        edge_type=edge.type.value,
        target_endpoint=target_endpoint,
        healthy=healthy,
        latency_ms=latency,
        message=message,
        criticality=edge.criticality.value,
        check_type=check_type,
        timestamp=timestamp,
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
    edges = resolver._edges
    if critical_only:
        edges = edges.critical_edges()

    results = []
    for edge in edges:
        result = check_edge_health(edge, resolver, timeout)
        results.append(result)

    return results
