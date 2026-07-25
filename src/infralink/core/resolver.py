"""Edge resolution for template rendering."""

from __future__ import annotations

import re
import warnings
from typing import Any
from urllib.parse import quote, quote_plus

from infralink.core.edges import Edge, EdgeSet
from infralink.core.registry import Host, Registry

_SAFE_SECRET_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


class ResolutionError(Exception):
    """Raised when edge resolution fails."""

    pass


class EdgeResolver:
    """
    Resolves edge targets for template rendering.

    Provides methods to get connection details (IPs, ports, URLs)
    from declared edges, eliminating hardcoded values in templates.
    """

    def __init__(self, registry: Registry, edges: EdgeSet) -> None:
        self._registry = registry
        self._edges = edges

    def get_edge(self, edge_id: str) -> Edge:
        """Get edge by ID, raising if not found."""
        edge = self._edges.get(edge_id)
        if not edge:
            raise ResolutionError(f"Edge not found: {edge_id}")
        return edge

    def get_target_host(self, edge_id: str) -> Host:
        """Get the target host for an edge."""
        edge = self.get_edge(edge_id)
        host = self._registry.get_by_uuid(edge.target_host)
        if not host:
            raise ResolutionError(f"Target host not found for edge {edge_id}: {edge.target_host}")
        return host

    def get_target_ip(self, edge_id: str, prefer: str = "tailscale") -> str:
        """Get the target IP address for an edge."""
        host = self.get_target_host(edge_id)
        ip = host.get_ip(prefer)
        if not ip:
            raise ResolutionError(
                f"No IP address available for edge {edge_id} target: {host.canonical_name}"
            )
        return ip

    def get_target_port(self, edge_id: str) -> int:
        """Get the target port for an edge."""
        edge = self.get_edge(edge_id)
        port = edge.target_port
        if port is None:
            raise ResolutionError(f"No target port declared for edge {edge_id}")
        return port

    def get_target_endpoint(self, edge_id: str, prefer: str = "tailscale") -> str:
        """Get target as 'ip:port' string."""
        ip = self.get_target_ip(edge_id, prefer)
        port = self.get_target_port(edge_id)
        return f"{ip}:{port}"

    def get_url(
        self,
        edge_id: str,
        *,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        path: str | None = None,
        query_params: dict[str, str] | None = None,
        prefer_ip: str = "tailscale",
    ) -> str:
        """
        Build a connection URL for an edge.

        Examples:
            resolver.get_url("airflow-to-postgres", user="airflow", password="secret", database="airflow")
            # Returns: postgresql+psycopg2://airflow:secret@100.78.109.111:5432/airflow

            resolver.get_url("otel-to-collector")
            # Returns: otlp://100.91.20.46:4317
        """
        _warn_legacy_url_method("get_url")
        password = _require_plain_secret(password)
        edge = self.get_edge(edge_id)
        ip = self.get_target_ip(edge_id, prefer_ip)
        port = self.get_target_port(edge_id)
        protocol = edge.protocol or "tcp"

        # Build URL
        if user and password:
            # URL-encode password in case of special characters
            encoded_password = quote_plus(password)
            auth = f"{user}:{encoded_password}@"
        elif user:
            auth = f"{user}@"
        else:
            auth = ""

        url = f"{protocol}://{auth}{ip}:{port}"

        if database:
            url = f"{url}/{database}"

        if path:
            url = f"{url}{path}"

        if query_params:
            query_string = "&".join(f"{k}={quote_plus(v)}" for k, v in query_params.items())
            url = f"{url}?{query_string}"

        return url

    def get_redis_url(
        self,
        edge_id: str,
        *,
        password: str | None = None,
        db: int = 0,
        prefer_ip: str = "tailscale",
    ) -> str:
        """Build a Redis connection URL."""
        _warn_legacy_url_method("get_redis_url")
        password = _require_plain_secret(password)
        ip = self.get_target_ip(edge_id, prefer_ip)
        port = self.get_target_port(edge_id)

        if password:
            return f"redis://:{quote_plus(password)}@{ip}:{port}/{db}"
        return f"redis://{ip}:{port}/{db}"

    def get_postgres_url(
        self,
        edge_id: str,
        *,
        user: str,
        password: str,
        database: str,
        driver: str = "postgresql+psycopg2",
        prefer_ip: str = "tailscale",
    ) -> str:
        """Build a PostgreSQL connection URL."""
        _warn_legacy_url_method("get_postgres_url")
        encoded_secret = _require_plain_secret(password)
        ip = self.get_target_ip(edge_id, prefer_ip)
        port = self.get_target_port(edge_id)
        if encoded_secret is None:
            raise TypeError("Secret credentials must be plain strings")
        encoded_password = quote_plus(encoded_secret)
        return f"{driver}://{user}:{encoded_password}@{ip}:{port}/{database}"

    def get_mysql_url(
        self,
        edge_id: str,
        *,
        user: str,
        password: str,
        database: str,
        driver: str = "mysql+pymysql",
        prefer_ip: str = "tailscale",
    ) -> str:
        """Build a MySQL/MariaDB connection URL."""
        _warn_legacy_url_method("get_mysql_url")
        encoded_secret = _require_plain_secret(password)
        ip = self.get_target_ip(edge_id, prefer_ip)
        port = self.get_target_port(edge_id)
        if encoded_secret is None:
            raise TypeError("Secret credentials must be plain strings")
        encoded_password = quote_plus(encoded_secret)
        return f"{driver}://{user}:{encoded_password}@{ip}:{port}/{database}"

    def get_connection_template(
        self,
        edge_id: str,
        *,
        user: str | None = None,
        database: str | None = None,
        prefer_ip: str | bool = True,
    ) -> str | None:
        """Build a safe connection template containing no plaintext secret."""
        edge = self.get_edge(edge_id)
        preference = _normalize_ip_preference(prefer_ip)
        ip = self.get_target_ip(edge_id, preference)
        port = self.get_target_port(edge_id)
        protocol = (edge.protocol or "").lower()

        if not (
            protocol.startswith(("postgres", "mysql", "mariadb")) or protocol in {"redis", "rediss"}
        ):
            return None

        declared_auth = edge._schema.auth
        resolved_user = user if user is not None else declared_auth.username
        resolved_database = database if database is not None else declared_auth.database
        encoded_user = _encode_coordinate(resolved_user, "user")
        encoded_database = _encode_coordinate(resolved_database, "database")
        placeholder = _secret_placeholder(edge.secret_ref)

        if protocol in {"redis", "rediss"}:
            redis_database = encoded_database if encoded_database is not None else "0"
            auth = _template_auth(encoded_user, placeholder, password_only=True)
            return f"{edge.protocol}://{auth}{ip}:{port}/{redis_database}"

        auth = _template_auth(encoded_user, placeholder)
        path = f"/{encoded_database}" if encoded_database is not None else ""
        return f"{edge.protocol}://{auth}{ip}:{port}{path}"

    def resolve_source_hosts(self, edge_id: str) -> list[Host]:
        """
        Resolve all source hosts for an edge.

        Handles both explicit host lists and selector-based matching.
        """
        edge = self.get_edge(edge_id)

        # Explicit host list
        if not edge.is_wildcard_source() and edge.source_hosts:
            hosts = []
            for uuid in edge.source_hosts:
                host = self._registry.get_by_uuid(uuid)
                if host:
                    hosts.append(host)
            return hosts

        # Selector-based matching
        if edge.source_selector:
            selector = edge.source_selector
            # Support role-based selection
            if "role" in selector:
                return self._registry.hosts_with_role(selector["role"])
            # Support service-based selection
            if "service" in selector:
                return self._registry.hosts_with_service(selector["service"])
            # Support observability.ready selection
            if "observability.ready" in selector:
                return [
                    h
                    for h in self._registry.active_hosts()
                    if h.to_dict().get("observability", {}).get("ready")
                ]

        # Wildcard - return all active hosts
        if edge.is_wildcard_source():
            return self._registry.active_hosts()

        return []

    def to_template_context(
        self, edge_id: str, secrets: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Build a template context dictionary for an edge.

        Useful for Jinja2 template rendering.
        """
        edge = self.get_edge(edge_id)
        target_host = self.get_target_host(edge_id)
        target_port = self.get_target_port(edge_id)

        context = {
            "edge_id": edge.id,
            "edge_type": edge.type.value,
            "target_ip": target_host.get_ip("tailscale"),
            "target_public_ip": target_host.public_ip,
            "target_port": target_port,
            "target_service": edge.target_service,
            "target_host_name": target_host.canonical_name,
            "target_host_uuid": target_host.uuid,
            "protocol": edge.protocol,
            "endpoint": f"{target_host.get_ip('tailscale')}:{target_port}",
            "auth_type": getattr(edge, "auth_type", "none"),
            "secret_ref": getattr(edge, "secret_ref", None),
            "username": getattr(edge._schema.auth, "username", None)
            if hasattr(edge, "_schema") and hasattr(edge._schema, "auth")
            else None,
        }

        # Add resolved URLs if secrets provided
        if secrets and edge.secret_ref and edge.secret_ref in secrets:
            context["password"] = _require_resolved_secret(secrets[edge.secret_ref])

        return context

    def validate_all(self) -> tuple[list[str], list[str]]:
        """
        Validate all edges can be resolved and check for best practices.

        Returns tuple of (errors, warnings).
        """
        errors = []
        warnings = []
        for edge in self._edges:
            try:
                self.get_target_host(edge.id)
            except ResolutionError as e:
                errors.append(str(e))

            # Advisory: Check for explicit health checks
            if not edge.healthcheck.explicit:
                warnings.append(
                    f"Edge {edge.id} ({edge.type.value}) is missing an explicit healthcheck definition."
                )

        return errors, warnings


def _warn_legacy_url_method(method_name: str) -> None:
    warnings.warn(
        f"{method_name}() is deprecated; use get_connection_template()",
        DeprecationWarning,
        stacklevel=2,
    )


def _require_plain_secret(value: object | None) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError("Secret credentials must be plain strings; call reveal() explicitly")
    return value


def _require_resolved_secret(value: object) -> str:
    if type(value) is not str:
        raise TypeError("Secret credentials must be plain strings; call reveal() explicitly")
    return value


def _normalize_ip_preference(prefer_ip: str | bool) -> str:
    if prefer_ip is True:
        return "tailscale"
    if prefer_ip is False:
        return "public"
    if type(prefer_ip) is not str:
        raise TypeError("IP preference must be a string or boolean")
    return prefer_ip


def _encode_coordinate(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError(f"Connection template {name} must be a plain string")
    return quote(value, safe="")


def _secret_placeholder(secret_ref: str | None) -> str | None:
    if secret_ref is None:
        return None
    if not _SAFE_SECRET_REF.fullmatch(secret_ref):
        raise ResolutionError("Edge has an unsafe secret reference")
    return f"${{secret:{secret_ref}}}"


def _template_auth(
    user: str | None,
    placeholder: str | None,
    *,
    password_only: bool = False,
) -> str:
    if placeholder is not None:
        if user is not None:
            return f"{user}:{placeholder}@"
        if password_only:
            return f":{placeholder}@"
        return f":{placeholder}@"
    if user is not None:
        return f"{user}@"
    return ""
