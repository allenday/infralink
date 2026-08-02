"""Edge resolution for template rendering."""

from __future__ import annotations

import ipaddress
import re
import warnings
from typing import Any
from urllib.parse import quote, quote_plus

from infralink.core.edges import Edge, EdgeSet
from infralink.core.errors import ResolutionError as ResolutionError
from infralink.core.registry import Host, Registry
from infralink.core.schema import SAFE_SECRET_REF_PATTERN

_DATABASE_SCHEME = re.compile(
    r"(?P<base>postgres|postgresql|mysql|mariadb)"
    r"(?:\+(?P<driver>[A-Za-z0-9][A-Za-z0-9.-]*))?\Z",
    re.ASCII | re.IGNORECASE,
)
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*\Z", re.ASCII)
_REDIS_SCHEME = re.compile(r"redis|rediss", re.ASCII | re.IGNORECASE)
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z", re.ASCII)
_SUPPORTED_SCHEME_PREFIXES = (
    "postgres",
    "postgresql",
    "mysql",
    "mariadb",
    "redis",
    "rediss",
)


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
        if type(port) is not int or not 1 <= port <= 65535:
            raise ResolutionError("Invalid target port: expected an integer from 1 to 65535")
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
        host = _format_authority_host(self.get_target_ip(edge_id, prefer_ip))
        port = self.get_target_port(edge_id)
        protocol = _normalize_uri_scheme(edge.protocol or "tcp")

        # Build URL
        if user and password:
            encoded_user = _encode_uri_component(user, "user")
            encoded_password = _encode_uri_component(password, "password")
            auth = f"{encoded_user}:{encoded_password}@"
        elif user:
            auth = f"{_encode_uri_component(user, 'user')}@"
        else:
            auth = ""

        url = f"{protocol}://{auth}{host}:{port}"

        if database:
            url = f"{url}/{_encode_uri_component(database, 'database')}"

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
        db: int | str = 0,
        driver: str = "redis",
        prefer_ip: str = "tailscale",
    ) -> str:
        """Build a Redis connection URL."""
        _warn_legacy_url_method("get_redis_url")
        password = _require_plain_secret(password)
        scheme = _normalize_redis_scheme(driver)
        database = _normalize_redis_database(db)
        host = _format_authority_host(self.get_target_ip(edge_id, prefer_ip))
        port = self.get_target_port(edge_id)

        if password:
            encoded_password = _encode_uri_component(password, "password")
            return f"{scheme}://:{encoded_password}@{host}:{port}/{database}"
        return f"{scheme}://{host}:{port}/{database}"

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
        scheme = _normalize_database_scheme(driver, {"postgres", "postgresql"})
        host = _format_authority_host(self.get_target_ip(edge_id, prefer_ip))
        port = self.get_target_port(edge_id)
        if encoded_secret is None:
            raise TypeError("Secret credentials must be plain strings")
        encoded_user = _encode_uri_component(user, "user")
        encoded_password = _encode_uri_component(encoded_secret, "password")
        encoded_database = _encode_uri_component(database, "database")
        return f"{scheme}://{encoded_user}:{encoded_password}@{host}:{port}/{encoded_database}"

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
        scheme = _normalize_database_scheme(driver, {"mysql", "mariadb"})
        host = _format_authority_host(self.get_target_ip(edge_id, prefer_ip))
        port = self.get_target_port(edge_id)
        if encoded_secret is None:
            raise TypeError("Secret credentials must be plain strings")
        encoded_user = _encode_uri_component(user, "user")
        encoded_password = _encode_uri_component(encoded_secret, "password")
        encoded_database = _encode_uri_component(database, "database")
        return f"{scheme}://{encoded_user}:{encoded_password}@{host}:{port}/{encoded_database}"

    def get_connection_template(
        self,
        edge_id: str,
        *,
        user: str | None = None,
        database: str | int | None = None,
        prefer_ip: str | bool = True,
    ) -> str | None:
        """Build a secret-substitution template, not a directly parseable URI.

        The ``${secret:<ref>}`` placeholder must be replaced with an encoded
        credential before parsing the final URI.
        """
        edge = self.get_edge(edge_id)
        scheme = _normalize_connection_scheme(edge.protocol)
        if scheme is None:
            return None

        declared_auth = edge._schema.auth
        if declared_auth.type in {"token", "certificate"}:
            return None

        preference = _normalize_ip_preference(prefer_ip)
        host = _format_authority_host(self.get_target_ip(edge_id, preference))
        port = self.get_target_port(edge_id)

        resolved_user = (
            None
            if declared_auth.type == "none"
            else user
            if user is not None
            else declared_auth.username
        )
        resolved_database = database if database is not None else declared_auth.database
        encoded_user = _encode_coordinate(resolved_user, "user")
        placeholder = None if declared_auth.type == "none" else _secret_placeholder(edge.secret_ref)

        base_scheme = scheme.partition("+")[0]
        if base_scheme in {"redis", "rediss"}:
            redis_database = _normalize_redis_database(
                resolved_database if resolved_database is not None else 0
            )
            auth = _template_auth(encoded_user, placeholder, password_only=True)
            return f"{scheme}://{auth}{host}:{port}/{redis_database}"

        encoded_database = _encode_coordinate(resolved_database, "database")
        auth = _template_auth(encoded_user, placeholder)
        path = f"/{encoded_database}" if encoded_database is not None else ""
        return f"{scheme}://{auth}{host}:{port}{path}"

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


def _encode_coordinate(value: object, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise TypeError(f"Connection template {name} must be a plain string")
    return quote(value, safe="")


def _encode_uri_component(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"Connection URL {name} must be a plain string")
    return quote(value, safe="")


def _normalize_connection_scheme(protocol: str | None) -> str | None:
    if protocol is None or protocol == "":
        return None
    normalized = _normalize_uri_scheme(
        protocol,
        error_message="Edge has an invalid connection scheme",
    )
    if _DATABASE_SCHEME.fullmatch(normalized) is not None:
        return normalized
    if _REDIS_SCHEME.fullmatch(normalized) is not None:
        return normalized
    if normalized.startswith(_SUPPORTED_SCHEME_PREFIXES):
        raise ResolutionError("Edge has an invalid connection scheme")
    return None


def _normalize_uri_scheme(
    scheme: object,
    *,
    error_message: str = "Edge has an invalid URI scheme",
) -> str:
    if type(scheme) is not str or _URI_SCHEME.fullmatch(scheme) is None:
        raise ResolutionError(error_message)
    return scheme.lower()


def _normalize_database_scheme(driver: object, allowed_bases: set[str]) -> str:
    normalized = _normalize_uri_scheme(
        driver,
        error_message="Edge has an invalid database URI scheme",
    )
    match = _DATABASE_SCHEME.fullmatch(normalized)
    if match is None or match.group("base") not in allowed_bases:
        raise ResolutionError("Edge has an invalid database URI scheme")
    return normalized


def _normalize_redis_scheme(driver: object) -> str:
    normalized = _normalize_uri_scheme(
        driver,
        error_message="Edge has an invalid Redis URI scheme",
    )
    if _REDIS_SCHEME.fullmatch(normalized) is None:
        raise ResolutionError("Edge has an invalid Redis URI scheme")
    return normalized


def _normalize_redis_database(database: object) -> str:
    if type(database) is int and database >= 0:
        return str(database)
    if type(database) is str and re.fullmatch(r"[0-9]+", database, re.ASCII) is not None:
        return database.lstrip("0") or "0"
    raise ResolutionError("Edge has an invalid Redis database")


def _format_authority_host(host: object) -> str:
    if type(host) is not str or not host or "%" in host:
        raise ResolutionError("Edge has an invalid connection host")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            host.encode("ascii")
        except UnicodeEncodeError:
            raise ResolutionError("Edge has an invalid connection host") from None
        if len(host) > 253 or any(_DNS_LABEL.fullmatch(label) is None for label in host.split(".")):
            raise ResolutionError("Edge has an invalid connection host") from None
        return host.lower()

    if isinstance(address, ipaddress.IPv6Address):
        return f"[{address.compressed}]"
    return str(address)


def _secret_placeholder(secret_ref: str | None) -> str | None:
    if secret_ref is None:
        return None
    if not SAFE_SECRET_REF_PATTERN.fullmatch(secret_ref):
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
