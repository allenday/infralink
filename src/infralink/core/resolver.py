"""Edge resolution for template rendering."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from infralink.core.edges import Edge, EdgeSet
from infralink.core.registry import Host, Registry


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
            raise ResolutionError(
                f"Target host not found for edge {edge_id}: {edge.target_host}"
            )
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
        return edge.target_port

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
        edge = self.get_edge(edge_id)
        ip = self.get_target_ip(edge_id, prefer_ip)
        port = edge.target_port
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
        ip = self.get_target_ip(edge_id, prefer_ip)
        port = self.get_target_port(edge_id)
        encoded_password = quote_plus(password)
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
        ip = self.get_target_ip(edge_id, prefer_ip)
        port = self.get_target_port(edge_id)
        encoded_password = quote_plus(password)
        return f"{driver}://{user}:{encoded_password}@{ip}:{port}/{database}"

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

    def to_template_context(self, edge_id: str, secrets: dict[str, str] | None = None) -> dict[str, Any]:
        """
        Build a template context dictionary for an edge.

        Useful for Jinja2 template rendering.
        """
        edge = self.get_edge(edge_id)
        target_host = self.get_target_host(edge_id)

        context = {
            "edge_id": edge.id,
            "edge_type": edge.type.value,
            "target_ip": target_host.get_ip("tailscale"),
            "target_public_ip": target_host.public_ip,
            "target_port": edge.target_port,
            "target_service": edge.target_service,
            "target_host_name": target_host.canonical_name,
            "target_host_uuid": target_host.uuid,
            "protocol": edge.protocol,
            "endpoint": f"{target_host.get_ip('tailscale')}:{edge.target_port}",
        }

        # Add resolved URLs if secrets provided
        if secrets and edge.secret_ref and edge.secret_ref in secrets:
            context["password"] = secrets[edge.secret_ref]

        return context

    def validate_all(self) -> list[str]:
        """
        Validate all edges can be resolved.

        Returns list of error messages (empty if all valid).
        """
        errors = []
        for edge in self._edges:
            try:
                self.get_target_host(edge.id)
            except ResolutionError as e:
                errors.append(str(e))
        return errors
