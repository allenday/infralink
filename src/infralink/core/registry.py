"""Registry management for infrastructure hosts."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from infralink.core.application import ApplicationSet
from infralink.core.schema import HostSchema, HostStatus, RegistrySchema, RoleConfig, SlotBinding
from infralink.core.template import ServiceTemplateSet


class Host:
    """
    Represents an infrastructure host.

    The UUID is the primary identifier (immutable), passed separately
    from the host data since it's the dictionary key in the registry.
    """

    def __init__(
        self,
        uuid: str,
        data: dict[str, Any],
        tailnet_domain: str | None = None,
        templates: ServiceTemplateSet | None = None,
    ) -> None:
        """
        Initialize a host.

        Args:
            uuid: The host's UUID (primary key from registry)
            data: Host configuration data (without uuid field)
            tailnet_domain: Optional tailnet domain
            templates: Optional service template set
        """
        self._uuid = uuid
        self._data = data
        self._schema = HostSchema(**data)
        self._tailnet_domain = tailnet_domain
        self._templates = templates or ServiceTemplateSet([], "1.0")

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
    def projects(self) -> list[str]:
        return self._schema.projects

    @property
    def group(self) -> str | None:
        """First project, retained for backward compatibility."""
        return self.projects[0] if self.projects else None

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
    def bws_project(self) -> str | None:
        """Primary secret project declared for this host."""
        return self._schema.bws_project

    @property
    def bws_machine_account(self) -> str | None:
        """Machine account declared for this host."""
        return self._schema.bws_machine_account

    @property
    def bws_extra_projects(self) -> tuple[str, ...]:
        """Additional secret projects declared for this host."""
        return tuple(self._schema.bws_extra_projects)

    @property
    def bws_projects(self) -> tuple[str, ...]:
        """Canonical Bitwarden project aliases required by a bootstrap target."""
        return tuple(self._schema.bws_projects)

    @property
    def controller_bootstrap(self) -> dict[str, Any] | None:
        """Canonical controller bootstrap declaration, when this host is provisioned."""
        return self._schema.controller_bootstrap

    @property
    def bootstrap_executor(self) -> dict[str, str] | None:
        """Declared immutable executor for an explicit host baseline apply."""
        if self._schema.bootstrap_executor is None:
            return None
        return self._schema.bootstrap_executor.model_dump()

    @property
    def self_deploy_v2_registry_layout_enabled(self) -> bool:
        """Whether this host's declared migration policy requires the V2 checkout root."""
        return bool((self._schema.model_extra or {}).get("self_deploy_v2_registry_layout_enabled"))

    @property
    def self_deploy_v2_reconcile_enabled(self) -> bool:
        """Whether this host declares V2 reconciliation as an active requirement."""
        return bool((self._schema.model_extra or {}).get("self_deploy_v2_reconcile_enabled", True))

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
    def templated_services(self) -> dict[str, Any]:
        """Services derived from host templates."""
        result = {}
        for template_id in self._schema.templates:
            template = self._templates.get_template(template_id)
            if template:
                for name, cfg in template.schema.services.items():
                    # Support overrides
                    overrides = (
                        self._schema.template_overrides.get(template_id, {})
                        .get("services", {})
                        .get(name, {})
                    )
                    data = cfg.model_dump()
                    data.update(overrides)
                    result[name] = data
        return result

    @property
    def services(self) -> dict[str, Any]:
        """All services (managed + unmanaged + templated). Backward compatible."""
        result = self.managed_services.copy()
        result.update(self.unmanaged_services)
        result.update(self.templated_services)
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
        """Check if host has a service (managed, unmanaged, or templated)."""
        return (
            service in self._schema.managed_services.keys()
            or service in self._schema.services.keys()
            or service in self._schema.unmanaged_services.keys()
            or service in self.templated_services.keys()
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
        result = self._schema.model_dump(by_alias=True)
        result["uuid"] = self._uuid
        return result

    def __repr__(self) -> str:
        return (
            f"Host({self.canonical_name}, uuid={self.uuid_prefix}..., status={self.status.value})"
        )


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
        applications: ApplicationSet | None = None,
        templates: ServiceTemplateSet | None = None,
    ) -> None:
        """
        Initialize registry.

        Args:
            hosts: Dictionary mapping UUID -> Host
            defaults: Ansible defaults configuration
            applications: Application groupings
            templates: Reusable service templates
        """
        self._hosts = hosts  # UUID -> Host
        self._defaults = defaults or {}
        self._tailnet_domain = tailnet_domain
        self._applications = applications or ApplicationSet([], "1.0")
        self._templates = templates or ServiceTemplateSet([], "1.0")
        # Secondary index: canonical_name -> Host
        self._name_index: dict[str, Host] = {h.canonical_name: h for h in hosts.values()}
        # Secondary index: uuid_prefix -> Host
        self._uuid_prefix_index: dict[str, Host] = {h.uuid_prefix: h for h in hosts.values()}

    @property
    def applications(self) -> ApplicationSet:
        """Get all application groupings."""
        return self._applications

    @property
    def templates(self) -> ServiceTemplateSet:
        """Get all reusable service templates."""
        return self._templates

    @classmethod
    def load(cls, path: str | Path) -> Registry:
        """Load registry from YAML file."""
        path = Path(path)
        with path.open() as f:
            data = yaml.safe_load(f)

        # Validate with schema
        schema = RegistrySchema(**data)

        # Load templates and applications from same directory if they exist
        templates = ServiceTemplateSet.load(path.parent / "service_templates.yml")
        apps = ApplicationSet.load(path.parent / "applications.yml")

        # UUID is the key, data is the value
        hosts = {
            uuid: Host(uuid, host.model_dump(), schema.tailnet_domain, templates)
            for uuid, host in schema.hosts.items()
        }

        return cls(
            hosts,
            schema.ansible_defaults,
            schema.tailnet_domain,
            applications=apps,
            templates=templates,
        )

    @classmethod
    def load_dir(cls, root: str | Path, pattern: str = "**/manifest.yml") -> Registry:
        """Load registry from a directory of per-host manifest files.

        Expects files named `manifest.yml` under `root` (any depth by default).
        Each manifest should have a top-level `hosts:` mapping of UUID → host data.
        """
        root_path = Path(root)
        hosts: dict[str, Host] = {}
        tailnet_domain: str | None = None

        # Load service_templates.yml if it exists in root
        templates = ServiceTemplateSet.load(root_path / "service_templates.yml")

        for manifest in sorted(root_path.glob(pattern)):
            with manifest.open() as f:
                data = yaml.safe_load(f) or {}

            # Prefer tailnet_domain if provided in any manifest
            if not tailnet_domain:
                tailnet_domain = data.get("tailnet_domain")

            for uuid, host_data in (data.get("hosts") or {}).items():
                host_schema = HostSchema(**host_data)
                hosts[uuid] = Host(uuid, host_schema.model_dump(), tailnet_domain, templates)

        # Load applications.yml if it exists in root
        apps = ApplicationSet.load(root_path / "applications.yml")

        return cls(
            hosts,
            defaults=None,
            tailnet_domain=tailnet_domain,
            applications=apps,
            templates=templates,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Registry:
        """Create registry from dictionary."""
        hosts_data = data.get("hosts", {})
        tailnet_domain = data.get("tailnet_domain")

        # Load templates and applications if provided in dict
        templates_data = data.get("templates")
        templates = ServiceTemplateSet.from_dict(templates_data) if templates_data else None

        apps_data = data.get("applications")
        apps = ApplicationSet.from_dict(apps_data) if apps_data else None

        # UUID is the key
        hosts = {uuid: Host(uuid, h, tailnet_domain, templates) for uuid, h in hosts_data.items()}
        return cls(hosts, data.get("ansible_defaults"), tailnet_domain, apps, templates)

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


def _service_names_for_host(host: HostSchema) -> set[str]:
    return (
        set(host.managed_services.keys())
        | set(host.services.keys())
        | set(host.unmanaged_services.keys())
    )


def validate_role_slots(hosts: dict[str, HostSchema], roles: dict[str, RoleConfig]) -> list[str]:
    """Validate that all required role slots are bound."""
    errors: list[str] = []
    for _host_id, host in hosts.items():
        for role_name in host.roles:
            role = roles.get(role_name)
            if role is None:
                continue
            overrides = host.role_overrides.get(role_name, {})
            slot_bindings = overrides.get("slots", {})
            for slot_name, slot in role.slots.items():
                if not slot.required:
                    continue
                binding_data = slot_bindings.get(slot_name)
                if not binding_data:
                    errors.append(
                        f"Host {host.canonical_name} role {role_name} missing required slot {slot_name}"
                    )
                    continue
                try:
                    binding = (
                        binding_data
                        if isinstance(binding_data, SlotBinding)
                        else SlotBinding(**binding_data)
                    )
                except Exception as exc:
                    errors.append(
                        f"Host {host.canonical_name} role {role_name} slot {slot_name} invalid: {exc}"
                    )
                    continue
                target_host = hosts.get(binding.host)
                if target_host is None:
                    errors.append(
                        f"Host {host.canonical_name} role {role_name} slot {slot_name} "
                        f"targets unknown host {binding.host}"
                    )
                    continue
                if binding.service not in _service_names_for_host(target_host):
                    errors.append(
                        f"Host {host.canonical_name} role {role_name} slot {slot_name} "
                        f"targets unknown service {binding.service} on {binding.host}"
                    )
    return errors


def validate_template_slots(
    hosts: dict[str, HostSchema], templates: ServiceTemplateSet
) -> list[str]:
    """Validate that all required template slots are bound."""
    errors: list[str] = []
    for _host_id, host in hosts.items():
        for template_id in host.templates:
            template = templates.get_template(template_id)
            if template is None:
                continue
            overrides = host.template_overrides.get(template_id, {})
            slot_bindings = overrides.get("slot_bindings", {})
            for slot_name, slot in template.schema.slots.items():
                if not slot.required:
                    continue
                binding_data = slot_bindings.get(slot_name)
                if not binding_data:
                    errors.append(
                        f"Host {host.canonical_name} template {template_id} missing required slot {slot_name}"
                    )
                    continue
                try:
                    binding = (
                        binding_data
                        if isinstance(binding_data, SlotBinding)
                        else SlotBinding(**binding_data)
                    )
                except Exception as exc:
                    errors.append(
                        f"Host {host.canonical_name} template {template_id} slot {slot_name} invalid: {exc}"
                    )
                    continue
                target_host = hosts.get(binding.host)
                if target_host is None:
                    errors.append(
                        f"Host {host.canonical_name} template {template_id} slot {slot_name} "
                        f"targets unknown host {binding.host}"
                    )
                    continue
                if binding.service not in _service_names_for_host(target_host):
                    errors.append(
                        f"Host {host.canonical_name} template {template_id} slot {slot_name} "
                        f"targets unknown service {binding.service} on {binding.host}"
                    )
    return errors
