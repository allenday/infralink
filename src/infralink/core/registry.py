"""Registry management for infrastructure hosts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import yaml

from infralink.core.schema import HostSchema, HostStatus, RegistrySchema


class Host:
    """Represents an infrastructure host."""

    def __init__(self, name: str, data: dict[str, Any]) -> None:
        self._name = name
        self._data = data
        self._schema = HostSchema(canonical_name=data.get("canonical_name", name), **data)

    @property
    def uuid(self) -> str:
        return self._schema.uuid

    @property
    def uuid_prefix(self) -> str:
        """First 8 characters of UUID."""
        return self.uuid[:8]

    @property
    def name(self) -> str:
        return self._name

    @property
    def canonical_name(self) -> str:
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
    def public_ip(self) -> str | None:
        return self._schema.public_ip

    @property
    def services(self) -> list[str]:
        return self._schema.services

    @property
    def roles(self) -> dict[str, dict[str, Any]]:
        return self._schema.roles

    def has_role(self, role: str) -> bool:
        return role in self._schema.roles

    def has_service(self, service: str) -> bool:
        return service in self._schema.services

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
        return self._data.copy()

    def __repr__(self) -> str:
        return f"Host({self.canonical_name}, uuid={self.uuid_prefix}..., status={self.status.value})"


class Registry:
    """
    Infrastructure host registry.

    Loads and manages host definitions from a YAML registry file.
    """

    def __init__(self, hosts: dict[str, Host], defaults: dict[str, Any] | None = None) -> None:
        self._hosts = hosts
        self._defaults = defaults or {}
        self._uuid_index: dict[str, Host] = {h.uuid: h for h in hosts.values()}
        self._uuid_prefix_index: dict[str, Host] = {h.uuid_prefix: h for h in hosts.values()}

    @classmethod
    def load(cls, path: str | Path) -> Registry:
        """Load registry from YAML file."""
        path = Path(path)
        with path.open() as f:
            data = yaml.safe_load(f)

        # Validate with schema
        schema = RegistrySchema(**data)

        hosts = {name: Host(name, host.model_dump()) for name, host in schema.hosts.items()}

        return cls(hosts, schema.ansible_defaults)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Registry:
        """Create registry from dictionary."""
        hosts_data = data.get("hosts", {})
        hosts = {name: Host(name, h) for name, h in hosts_data.items()}
        return cls(hosts, data.get("ansible_defaults"))

    def get_by_uuid(self, uuid: str) -> Host | None:
        """Get host by full UUID."""
        return self._uuid_index.get(uuid)

    def get_by_uuid_prefix(self, prefix: str) -> Host | None:
        """Get host by UUID prefix (first 8 chars)."""
        # Try exact prefix match first
        if prefix in self._uuid_prefix_index:
            return self._uuid_prefix_index[prefix]
        # Try partial match
        for full_uuid, host in self._uuid_index.items():
            if full_uuid.startswith(prefix):
                return host
        return None

    def get_by_name(self, name: str) -> Host | None:
        """Get host by registry name or canonical name."""
        if name in self._hosts:
            return self._hosts[name]
        for host in self._hosts.values():
            if host.canonical_name == name:
                return host
        return None

    def get(self, identifier: str) -> Host | None:
        """Get host by UUID, UUID prefix, or name."""
        # Try UUID first
        if host := self.get_by_uuid(identifier):
            return host
        # Try UUID prefix
        if host := self.get_by_uuid_prefix(identifier):
            return host
        # Try name
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
