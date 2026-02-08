"""Registry management for infrastructure hosts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import yaml

from infralink.core.schema import HostSchema, HostStatus, RegistrySchema


class Host:
    """
    Represents an infrastructure host.

    The UUID is the primary identifier (immutable), passed separately
    from the host data since it's the dictionary key in the registry.
    """

    def __init__(self, uuid: str, data: dict[str, Any], tailnet_domain: str | None = None) -> None:
        """
        Initialize a host.

        Args:
            uuid: The host's UUID (primary key from registry)
            data: Host configuration data (without uuid field)
        """
        self._uuid = uuid
        self._data = data
        self._schema = HostSchema(**data)
        self._tailnet_domain = tailnet_domain

    @property
    def uuid(self) -> str:
        """The host's UUID (primary identifier, immutable)."""
        return self._uuid

    @property
    def uuid_prefix(self) -> str:
        """First 8 characters of UUID."""
        return self._uuid[:8]

    @property
    def canonical_name(self) -> str:
        """Human-readable name for the host."""
        return self._schema.canonical_name

    @property
    def status(self) -> HostStatus:
        return self._schema.status

    @property
    def is_active(self) -> bool:
        return self._schema.status == HostStatus.ACTIVE

    @property
    def group(self) -> str | None:
        return self._schema.group

    @property
    def cloud(self) -> str | None:
        return self._schema.cloud

    @property
    def tailscale_ip(self) -> str | None:
        return self._schema.tailscale_ip

    @property
    def tailscale_name(self) -> str | None:
        return self._schema.tailscale_name

    @property
    def magicdns_name(self) -> str | None:
        if self._schema.magicdns_name:
            return self._schema.magicdns_name
        if self.tailscale_name and self._tailnet_domain:
            return f"{self.tailscale_name}.{self._tailnet_domain}"
        return None

    @property
    def public_ip(self) -> str | None:
        return self._schema.public_ip

    @property
    def managed_services(self) -> dict[str, Any]:
        """Managed service configurations (in legacy docker-compose.yml.j2)."""
        # Prefer managed_services, fall back to services for backward compat
        if self._schema.managed_services:
            return {name: cfg.model_dump() for name, cfg in self._schema.managed_services.items()}
        return {name: cfg.model_dump() for name, cfg in self._schema.services.items()}

    @property
    def unmanaged_services(self) -> dict[str, Any]:
        """Unmanaged service configurations (not in legacy docker-compose.yml.j2)."""
        return {name: cfg.model_dump() for name, cfg in self._schema.unmanaged_services.items()}

    @property
    def unmanaged_roles(self) -> dict[str, Any]:
        """Unmanaged roles (not in legacy docker-compose.yml.j2)."""
        return {name: cfg.model_dump() for name, cfg in self._schema.unmanaged_roles.items()}

    @property
    def services(self) -> dict[str, Any]:
        """All services (managed + unmanaged). Backward compatible."""
        result = self.managed_services.copy()
        result.update(self.unmanaged_services)
        return result

    @property
    def service_names(self) -> list[str]:
        """List of all service names running on this host."""
        return list(self.services.keys())

    @property
    def managed_service_names(self) -> list[str]:
        """List of managed service names."""
        return list(self.managed_services.keys())

    @property
    def unmanaged_service_names(self) -> list[str]:
        """List of unmanaged service names."""
        return list(self.unmanaged_services.keys())

    def get_service(self, name: str) -> dict[str, Any] | None:
        """Get service config by name (checks both managed and unmanaged)."""
        if name in self._schema.managed_services:
            return self._schema.managed_services[name].model_dump()
        if name in self._schema.services:
            return self._schema.services[name].model_dump()
        if name in self._schema.unmanaged_services:
            return self._schema.unmanaged_services[name].model_dump()
        return None

    def get_service_port(self, name: str) -> int | None:
        """Get port for a service."""
        if name in self._schema.managed_services:
            return self._schema.managed_services[name].port
        if name in self._schema.services:
            return self._schema.services[name].port
        if name in self._schema.unmanaged_services:
            return self._schema.unmanaged_services[name].port
        return None

    @property
    def roles(self) -> list[str]:
        """List of roles this host fulfills."""
        return self._schema.roles

    @property
    def role_overrides(self) -> dict[str, dict[str, Any]]:
        """Role-specific configuration overrides."""
        return self._schema.role_overrides

    def has_role(self, role: str) -> bool:
        return role in self._schema.roles

    @property
    def public_ip_secondary(self) -> str | None:
        return self._schema.public_ip_secondary

    @property
    def use_exit_node(self) -> bool:
        return self._schema.use_exit_node

    @property
    def dns_hostnames(self) -> list[str]:
        return self._schema.dns_hostnames

    @property
    def provider_metadata(self) -> dict[str, Any]:
        return self._schema.provider_metadata

    @property
    def mounts(self) -> dict[str, dict[str, Any]]:
        return self._schema.mounts

    def has_service(self, service: str) -> bool:
        """Check if host has a service (managed or unmanaged)."""
        return (
            service in self._schema.managed_services.keys()
            or service in self._schema.services.keys()
            or service in self._schema.unmanaged_services.keys()
        )

    def get_ip(self, prefer: str = "tailscale") -> str | None:
        """Get IP address with preference order."""
        if prefer == "tailscale":
            return self.tailscale_ip or self.public_ip or self._schema.private_ip
        elif prefer == "public":
            return self.public_ip or self.tailscale_ip or self._schema.private_ip
        elif prefer == "private":
            return self._schema.private_ip or self.tailscale_ip or self.public_ip
        return self.tailscale_ip

    def to_dict(self) -> dict[str, Any]:
        """Return host data as dictionary (includes uuid)."""
        result = self._data.copy()
        result["uuid"] = self._uuid
        return result

    def __repr__(self) -> str:
        return f"Host({self.canonical_name}, uuid={self.uuid_prefix}..., status={self.status.value})"


class Registry:
    """
    Infrastructure host registry.

    Loads and manages host definitions from a YAML registry file.
    Uses UUID as the primary key for each host.
    """

    def __init__(
        self,
        hosts: dict[str, Host],
        defaults: dict[str, Any] | None = None,
        tailnet_domain: str | None = None,
    ) -> None:
        """
        Initialize registry.

        Args:
            hosts: Dictionary mapping UUID -> Host
            defaults: Ansible defaults configuration
        """
        self._hosts = hosts  # UUID -> Host
        self._defaults = defaults or {}
        self._tailnet_domain = tailnet_domain
        # Secondary index: canonical_name -> Host
        self._name_index: dict[str, Host] = {h.canonical_name: h for h in hosts.values()}
        # Secondary index: uuid_prefix -> Host
        self._uuid_prefix_index: dict[str, Host] = {h.uuid_prefix: h for h in hosts.values()}

    @classmethod
    def load(cls, path: str | Path) -> Registry:
        """Load registry from YAML file."""
        path = Path(path)
        with path.open() as f:
            data = yaml.safe_load(f)

        # Validate with schema
        schema = RegistrySchema(**data)

        # UUID is the key, data is the value
        hosts = {
            uuid: Host(uuid, host.model_dump(), schema.tailnet_domain)
            for uuid, host in schema.hosts.items()
        }

        return cls(hosts, schema.ansible_defaults, schema.tailnet_domain)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Registry:
        """Create registry from dictionary."""
        hosts_data = data.get("hosts", {})
        # UUID is the key
        tailnet_domain = data.get("tailnet_domain")
        hosts = {uuid: Host(uuid, h, tailnet_domain) for uuid, h in hosts_data.items()}
        return cls(hosts, data.get("ansible_defaults"), tailnet_domain)

    def get_by_uuid(self, uuid: str) -> Host | None:
        """Get host by full UUID (primary lookup)."""
        return self._hosts.get(uuid)

    def get_by_uuid_prefix(self, prefix: str) -> Host | None:
        """Get host by UUID prefix (first 8 chars)."""
        # Try exact prefix match first
        if prefix in self._uuid_prefix_index:
            return self._uuid_prefix_index[prefix]
        # Try partial match
        for full_uuid, host in self._hosts.items():
            if full_uuid.startswith(prefix):
                return host
        return None

    def get_by_name(self, name: str) -> Host | None:
        """Get host by canonical name."""
        return self._name_index.get(name)

    def get(self, identifier: str) -> Host | None:
        """Get host by UUID, UUID prefix, or canonical name."""
        # Try UUID first (most specific)
        if host := self.get_by_uuid(identifier):
            return host
        # Try UUID prefix
        if host := self.get_by_uuid_prefix(identifier):
            return host
        # Try canonical name
        return self.get_by_name(identifier)

    def filter(
        self,
        status: HostStatus | None = None,
        group: str | None = None,
        cloud: str | None = None,
        service: str | None = None,
        role: str | None = None,
    ) -> list[Host]:
        """Filter hosts by criteria."""
        results = []
        for host in self._hosts.values():
            if status and host.status != status:
                continue
            if group and host.group != group:
                continue
            if cloud and host.cloud != cloud:
                continue
            if service and not host.has_service(service):
                continue
            if role and not host.has_role(role):
                continue
            results.append(host)
        return results

    def active_hosts(self) -> list[Host]:
        """Get all active hosts."""
        return self.filter(status=HostStatus.ACTIVE)

    def hosts_with_role(self, role: str) -> list[Host]:
        """Get all hosts with a specific role."""
        return [h for h in self._hosts.values() if h.has_role(role)]

    def hosts_with_service(self, service: str) -> list[Host]:
        """Get all hosts running a specific service."""
        return [h for h in self._hosts.values() if h.has_service(service)]

    def groups(self) -> set[str]:
        """Get all unique groups."""
        return {h.group for h in self._hosts.values() if h.group}

    def clouds(self) -> set[str]:
        """Get all unique cloud providers."""
        return {h.cloud for h in self._hosts.values() if h.cloud}

    @property
    def defaults(self) -> dict[str, Any]:
        return self._defaults

    def __len__(self) -> int:
        return len(self._hosts)

    def __iter__(self) -> Iterator[Host]:
        return iter(self._hosts.values())

    def __contains__(self, item: str) -> bool:
        return self.get(item) is not None
