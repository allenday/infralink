"""Pydantic schemas for registry and edge validation."""

from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SAFE_SECRET_REF_PATTERN = re.compile(
    r"(?:[A-Za-z0-9]|_[A-Za-z0-9])[A-Za-z0-9._-]*"
    r"(?:/(?:[A-Za-z0-9]|_[A-Za-z0-9])[A-Za-z0-9._-]*)*\Z",
    re.ASCII,
)


class StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class HostStatus(str, Enum):
    """Host lifecycle status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    TERMINATED = "terminated"
    PROVISIONING = "provisioning"
    MAINTENANCE = "maintenance"


class HostBootstrapExecutorSchema(StrictModel):
    """Immutable Bastion-side executor reference for explicit host bootstrap."""

    repository: str = Field(pattern=r"^https://[^/@:?#\s]+/[^?#\s]+\.git$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./-]*\.json$")
    bws_machine_token_ref: str = Field(
        pattern=r"^(?:[A-Za-z0-9]|_[A-Za-z0-9])[A-Za-z0-9._-]*(?:/(?:[A-Za-z0-9]|_[A-Za-z0-9])[A-Za-z0-9._-]*)*$"
    )
    infra_read_deploy_key_ref: str = Field(
        pattern=r"^(?:[A-Za-z0-9]|_[A-Za-z0-9])[A-Za-z0-9._-]*(?:/(?:[A-Za-z0-9]|_[A-Za-z0-9])[A-Za-z0-9._-]*)*$"
    )


class NetworkConfig(StrictModel):
    """Network configuration for a host."""

    tailscale_ip: str | None = None
    tailscale_name: str | None = None
    public_ip: str | None = None
    public_ipv6: str | None = None
    private_ip: str | None = None


class ServiceProtocol(str, Enum):
    """Common service protocols."""

    TCP = "tcp"
    HTTP = "http"
    HTTPS = "https"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    REDIS = "redis"
    GRPC = "grpc"
    FASTCGI = "fastcgi"
    SMTP = "smtp"
    IRC = "irc"
    WEBSOCKET = "websocket"


class ServiceExposure(str, Enum):
    """How a service is exposed."""

    INTERNAL = "internal"  # Only accessible via tailscale/private network
    PUBLIC = "public"  # Exposed to internet
    LOCAL = "local"  # Only localhost


class NodeSchema(StrictModel):
    """Base schema for all node-like objects."""

    node_type: str
    canonical_name: str | None = None


class ServiceSchema(NodeSchema):
    """Service definition for catalog entries."""

    name: str
    group: str
    compose_service: str | None = None
    requires_secrets: list[str] = Field(default_factory=list)
    prom_check: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    node_type: str = "service"

    @model_validator(mode="after")
    def populate_canonical_name(self) -> ServiceSchema:
        if self.canonical_name is None:
            self.canonical_name = self.name
        return self


class ServiceConfig(StrictModel):
    """Service declaration on a host.

    Services are first-class objects with their own properties,
    enabling self-documenting registries, better health checks,
    and richer diagrams.
    """

    port: int | None = None
    protocol: str = "tcp"
    exposure: ServiceExposure = ServiceExposure.INTERNAL
    depends_on: list[str] = Field(default_factory=list)  # Local service dependencies
    replicas: int = 1  # Number of instances (ports increment: 8983, 8984, ...)
    notes: str | None = None


class UnmanagedServiceConfig(ServiceConfig):
    """Unmanaged service - same as ServiceConfig but with source tracking.

    Used for services discovered via SSH that are not in the legacy
    docker-compose.yml.j2 template. These need to be brought under
    gitops management.
    """

    source: str | None = None  # Path to compose file or "standalone (docker run)"


class UnmanagedRoleConfig(StrictModel):
    """Unmanaged role - a role not in the legacy docker-compose.yml.j2.

    Used for roles discovered via SSH that exist but are defined in
    a separate compose file. These need to be brought under gitops management.
    """

    source: str | None = None  # Path to compose file
    notes: str | None = None


class SlotConfig(StrictModel):
    """Role slot definition."""

    type: str
    required: bool = True
    description: str | None = None


class SlotBinding(StrictModel):
    """Binding for a role slot."""

    host: str
    service: str
    role: str | None = None


class RoleConfig(StrictModel):
    """Role definition - a contract for what a host should have.

    Roles define expected services, exporters, and secrets.
    Hosts declare roles; services are derived from roles.
    """

    services: dict[str, ServiceConfig] = Field(default_factory=dict)
    slots: dict[str, SlotConfig] = Field(default_factory=dict)
    required_secrets: list[str] = Field(default_factory=list)
    description: str | None = None


class ServiceTemplateSchema(StrictModel):
    """Template for a complex service deployment."""

    description: str | None = None
    services: dict[str, ServiceConfig] = Field(default_factory=dict)
    slots: dict[str, SlotConfig] = Field(default_factory=dict)
    requires_secrets: list[str] = Field(default_factory=list)
    exporters: list[str] = Field(default_factory=list)


class ServiceTemplateSetSchema(StrictModel):
    """Schema for the service_templates.yml file."""

    schema_version: str = "1.0"
    templates: dict[str, ServiceTemplateSchema]


class ProviderMetadata(BaseModel):
    """Cloud provider-specific metadata.

    Generic key-value for provider-specific fields like
    hcloud_project, robot_id, scaleway_server_id, etc.
    """

    model_config = {"extra": "allow"}  # Allow arbitrary fields


class ObservabilityConfig(StrictModel):
    """Observability configuration for a host.

    Tracks what's monitored vs what should be monitored.
    Health is derived from prometheus scrape success, not explicit probes.
    """

    model_config = ConfigDict(extra="allow")

    ready: bool = False  # Is this host fully observable?
    managed_services: list[str] = Field(default_factory=list)  # Monitored by prometheus
    unmanaged_services: list[str] = Field(default_factory=list)  # Not yet monitored
    missing_exporters: list[str] = Field(default_factory=list)  # Known gaps
    port_overrides: dict[str, int] = Field(default_factory=dict)  # Non-standard ports
    notes: str | None = None


def validate_uuid_format(uuid: str) -> bool:
    """Validate UUID format (loose check)."""
    parts = uuid.split("-")
    return len(parts) == 5


class HostSchema(NodeSchema):
    """
    Schema for a host in the registry.

    Note: The UUID is the dictionary key, not a field in the schema.
    This ensures UUID-as-primary-key semantics where the identity
    is immutable and separate from mutable fields like canonical_name.

    Design principles:
    - Roles define expected services (contracts)
    - Services are derived from roles, explicit only for one-offs
    - Provider-specific metadata in generic bucket
    - Health derived from prometheus scrape success
    """

    model_config = ConfigDict(extra="allow")

    # Identity (canonical_name is the human-readable identifier)
    canonical_name: str
    node_type: str = "host"
    status: HostStatus = HostStatus.ACTIVE
    projects: list[str] = Field(default_factory=list)
    cloud: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_group_to_projects(cls, data: Any) -> Any:
        if isinstance(data, dict):
            group = data.pop("group", None)
            projects = data.get("projects", [])
            if group and group not in projects:
                projects.append(group)
            data["projects"] = projects
        return data

    # Network
    tailscale_ip: str | None = None
    tailscale_name: str | None = None
    magicdns_name: str | None = None
    public_ip: str | None = None
    public_ip_secondary: str | None = None  # Failover/additional IP
    public_ipv6: str | None = None
    private_ip: str | None = None
    use_exit_node: bool = False  # Routes traffic through tailscale exit node
    dns_hostnames: list[str] = Field(default_factory=list)

    # Roles - declare what contracts this host fulfills
    # Services are derived from roles; role_overrides for customization
    roles: list[str] = Field(default_factory=list)
    role_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Templates - reusable service bundles
    templates: list[str] = Field(default_factory=list)
    template_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Unmanaged roles - roles discovered via SSH but not in legacy docker-compose.yml.j2
    # These need to be brought under gitops management
    unmanaged_roles: dict[str, UnmanagedRoleConfig] = Field(default_factory=dict)

    # Managed services - explicit services in legacy docker-compose.yml.j2 not covered by roles
    # Supports both new format (dict) and legacy format (list of strings)
    managed_services: dict[str, ServiceConfig] = Field(default_factory=dict)

    # Legacy alias for backward compatibility
    services: dict[str, ServiceConfig] = Field(default_factory=dict)

    # Unmanaged services - services discovered via SSH but not in legacy docker-compose.yml.j2
    # These need to be brought under gitops management
    unmanaged_services: dict[str, UnmanagedServiceConfig] = Field(default_factory=dict)

    @field_validator("managed_services", mode="before")
    @classmethod
    def normalize_managed_services(cls, v: dict[str, Any] | list[str]) -> dict[str, Any]:
        """Convert legacy list format to dict format."""
        if isinstance(v, list):
            # Legacy format: ["nginx", "postgresql"]
            # Convert to: {"nginx": {}, "postgresql": {}}
            return {name: {} for name in v}
        return v

    @field_validator("services", mode="before")
    @classmethod
    def normalize_services(cls, v: dict[str, Any] | list[str]) -> dict[str, Any]:
        """Convert legacy list format to dict format (backward compat)."""
        if isinstance(v, list):
            return {name: {} for name in v}
        return v

    @field_validator("roles", mode="before")
    @classmethod
    def normalize_roles(cls, v: dict[str, Any] | list[str]) -> list[str]:
        """Convert legacy dict format to list format."""
        if isinstance(v, dict):
            # Legacy format: {"airflow-worker": {"concurrency": 10}}
            # Convert to list, store config in role_overrides via separate validator
            return list(v.keys())
        return v

    # Secrets
    bws_project: str | None = None
    bws_machine_account: str | None = None
    bws_extra_projects: list[str] = Field(default_factory=list)
    bws_projects: list[str] = Field(default_factory=list)
    controller_bootstrap: dict[str, Any] | None = None
    bootstrap_executor: HostBootstrapExecutorSchema | None = None

    # Provider-specific metadata (hcloud_project, robot_id, etc.)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    # Mounts (storagebox, gcsfuse, etc.)
    mounts: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Observability config (what should be monitored)
    observability: ObservabilityConfig | None = None

    # Probes and TLS
    probe_path: str | None = None
    tls_certs: list[dict[str, Any]] = Field(default_factory=list)

    # WordPress email check configuration (pass-through)
    wordpress_email_check: dict[str, Any] | None = None

    # Metadata
    docker_version: str | None = None
    created: str | None = None
    updated: str | None = None
    legacy_instances: list[str] = Field(default_factory=list)  # Old ansible names
    notes: str | None = None


class RegistrySchema(StrictModel):
    """
    Schema for the complete host registry.

    The hosts dictionary uses UUID as the key (primary identifier).
    This ensures immutable identity - renaming a host only changes
    the canonical_name field, not the key.
    """

    hosts: dict[str, HostSchema]
    ansible_defaults: dict[str, Any] = Field(default_factory=dict)
    tailnet_domain: str | None = None

    @field_validator("hosts")
    @classmethod
    def validate_host_uuids(cls, v: dict[str, HostSchema]) -> dict[str, HostSchema]:
        """Validate that all host keys are valid UUIDs."""
        for key in v.keys():
            if not validate_uuid_format(key):
                raise ValueError(f"Invalid UUID format for host key: {key}")
        return v


# --- Edge Schemas ---


class EdgeType(str, Enum):
    """Types of edges between infrastructure nodes."""

    DATABASE = "database"
    QUEUE = "queue"
    CLUSTER = "cluster"
    TELEMETRY = "telemetry"
    MONITORING = "monitoring"
    API = "api"
    STORAGE = "storage"
    SSH = "ssh"
    SECURITY = "security"
    SMTP = "smtp"
    EMAIL = "email"
    IRC = "irc"
    XMPP = "xmpp"


class Criticality(str, Enum):
    """Edge criticality levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class HealthCheckType(str, Enum):
    """Types of health checks."""

    TCP = "tcp"
    HTTP = "http"
    HTTPS = "https"
    QUERY = "query"
    PING = "ping"
    API = "api"


class AuthConfig(StrictModel):
    """Authentication configuration for an edge.

    Extended fields support real-world edge patterns:
    - username/database/role for database edges
    - mount_path for storage edges (sshfs, NFS)
    - secret_ref for BWS/vault secret resolution
    """

    type: Literal["none", "password", "basic", "token", "certificate"] = "none"
    secret_ref: str | None = None
    username: str | None = None
    database: str | None = None
    role: Literal["ro", "rw", "admin", "relay"] | None = None
    mount_path: str | None = None

    @model_validator(mode="after")
    def validate_auth_credentials(self) -> AuthConfig:
        if (
            self.secret_ref is not None
            and SAFE_SECRET_REF_PATTERN.fullmatch(self.secret_ref) is None
        ):
            raise ValueError("auth requires a safe secret_ref")

        if self.type == "none":
            if self.secret_ref is not None:
                raise ValueError("none auth forbids secret_ref")
            return self

        if self.type in {"password", "basic", "token"}:
            if self.secret_ref is None:
                raise ValueError(f"{self.type} auth requires secret_ref")
            return self

        if self.type == "certificate":
            if self.mount_path is not None and not _is_safe_certificate_mount(self.mount_path):
                raise ValueError("certificate auth requires a safe absolute mount_path")
            if self.secret_ref is None and self.mount_path is None:
                raise ValueError(
                    "certificate auth requires secret_ref or a safe absolute mount_path"
                )
        return self


def _is_safe_certificate_mount(mount_path: str | None) -> bool:
    if (
        mount_path is None
        or len(mount_path) < 2
        or not mount_path.startswith("/")
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in mount_path
        )
    ):
        return False
    return ".." not in PurePosixPath(mount_path).parts


class HealthCheckConfig(StrictModel):
    """Health check configuration for an edge."""

    model_config = ConfigDict(extra="allow")

    type: HealthCheckType = HealthCheckType.TCP
    interval: str = "60s"
    timeout: str = "5s"
    path: str | None = None  # For HTTP checks
    query: str | None = None  # For query checks
    conditions: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    explicit: bool = Field(default=False, exclude=True)


class EdgeSourceSelector(StrictModel):
    """Selector for edge source hosts."""

    hosts: list[str] | Literal["*"] = Field(default_factory=list)
    selector: dict[str, Any] | None = None  # e.g., {"role": "airflow-worker"}
    service: str | None = None


class EdgeTarget(StrictModel):
    """Target endpoint for an edge."""

    host: str  # UUID
    service: str
    port: Annotated[int, Field(strict=True, ge=1, le=65535)] | None = None


class EdgeMetadata(StrictModel):
    """Metadata for an edge."""

    purpose: str | None = None
    criticality: Criticality = Criticality.MEDIUM
    dns_name: str | None = None
    notes: str | None = None

    model_config = ConfigDict(extra="allow")


class EdgeSchema(StrictModel):
    """Schema for an edge between infrastructure nodes."""

    id: str
    type: EdgeType
    projects: list[str] = Field(default_factory=list)
    from_: EdgeSourceSelector = Field(alias="from")
    to: EdgeTarget
    protocol: str | None = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    healthcheck: HealthCheckConfig | None = None
    metadata: EdgeMetadata = Field(default_factory=EdgeMetadata)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def migrate_group_to_projects(cls, data: Any) -> Any:
        if isinstance(data, dict):
            group = data.pop("group", None)
            projects = data.get("projects", [])
            if group and group not in projects:
                projects.append(group)
            data["projects"] = projects
        return data

    @field_validator("id")
    @classmethod
    def validate_edge_id_is_uuid(cls, v: str) -> str:
        """Validate that edge ID is a valid UUID."""
        if not validate_uuid_format(v):
            raise ValueError(f"Edge ID must be a valid UUID, got: {v}")
        return v


class EdgeSetSchema(StrictModel):
    """Schema for the complete edge set."""

    schema_version: str = "1.0"
    edge_types: dict[str, dict[str, Any]] = Field(default_factory=dict)
    edges: list[EdgeSchema]


# --- Application Schemas ---


class ApplicationMember(StrictModel):
    """Member of an application."""

    host: str  # UUID
    services: list[str] = Field(default_factory=list)


class ApplicationHealthStrategy(str, Enum):
    """Strategy for calculating application health."""

    ALL = "all"
    ALL_CRITICAL = "all-critical"
    ANY = "any"


class ApplicationHealthConfig(StrictModel):
    """Health configuration for an application."""

    strategy: ApplicationHealthStrategy = ApplicationHealthStrategy.ALL_CRITICAL


class ApplicationSchema(StrictModel):
    """Logical grouping of hosts, services, and edges."""

    description: str | None = None
    members: list[ApplicationMember]
    edges: Literal["auto"] | list[str] = "auto"
    health: ApplicationHealthConfig = Field(default_factory=ApplicationHealthConfig)


class ApplicationSetSchema(StrictModel):
    """Schema for the applications.yml file."""

    schema_version: str = "1.0"
    applications: dict[str, ApplicationSchema]
