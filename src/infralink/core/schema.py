"""Pydantic schemas for registry and edge validation."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class HostStatus(str, Enum):
    """Host lifecycle status."""

    ACTIVE = "active"
    TERMINATED = "terminated"
    PROVISIONING = "provisioning"
    MAINTENANCE = "maintenance"


class NetworkConfig(BaseModel):
    """Network configuration for a host."""

    tailscale_ip: str | None = None
    tailscale_name: str | None = None
    public_ip: str | None = None
    public_ipv6: str | None = None
    private_ip: str | None = None


class ServiceConfig(BaseModel):
    """Service declaration on a host."""

    name: str
    port: int | None = None
    protocol: str = "tcp"
    healthcheck_path: str | None = None


class ObservabilityConfig(BaseModel):
    """Observability configuration for a host."""

    ready: bool = False
    managed_services: list[str] = Field(default_factory=list)
    unmanaged_services: list[str] = Field(default_factory=list)
    port_overrides: dict[str, int] = Field(default_factory=dict)
    notes: str | None = None


class HostSchema(BaseModel):
    """Schema for a host in the registry."""

    uuid: str
    canonical_name: str
    status: HostStatus = HostStatus.ACTIVE
    group: str | None = None
    cloud: str | None = None

    # Network
    tailscale_ip: str | None = None
    tailscale_name: str | None = None
    public_ip: str | None = None
    public_ipv6: str | None = None
    private_ip: str | None = None

    # Services
    services: list[str] = Field(default_factory=list)
    roles: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Secrets
    bws_project: str | None = None
    bws_machine_account: str | None = None
    bws_extra_projects: list[str] = Field(default_factory=list)

    # Observability
    observability: ObservabilityConfig | None = None

    # Metadata
    dns_hostnames: list[str] = Field(default_factory=list)
    docker_version: str | None = None
    created: str | None = None
    updated: str | None = None

    @field_validator("uuid")
    @classmethod
    def validate_uuid_format(cls, v: str) -> str:
        """Validate UUID format (loose check)."""
        parts = v.split("-")
        if len(parts) != 5:
            raise ValueError(f"Invalid UUID format: {v}")
        return v


class RegistrySchema(BaseModel):
    """Schema for the complete host registry."""

    hosts: dict[str, HostSchema]
    ansible_defaults: dict[str, Any] = Field(default_factory=dict)


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


class AuthConfig(BaseModel):
    """Authentication configuration for an edge."""

    type: Literal["none", "password", "basic", "token", "certificate"] = "none"
    secret_ref: str | None = None


class HealthCheckConfig(BaseModel):
    """Health check configuration for an edge."""

    type: HealthCheckType = HealthCheckType.TCP
    interval: str = "60s"
    timeout: str = "5s"
    path: str | None = None  # For HTTP checks
    query: str | None = None  # For query checks


class EdgeSourceSelector(BaseModel):
    """Selector for edge source hosts."""

    hosts: list[str] | Literal["*"] = Field(default_factory=list)
    selector: dict[str, Any] | None = None  # e.g., {"role": "airflow-worker"}
    service: str | None = None


class EdgeTarget(BaseModel):
    """Target endpoint for an edge."""

    host: str  # UUID
    service: str
    port: int


class EdgeMetadata(BaseModel):
    """Metadata for an edge."""

    purpose: str | None = None
    criticality: Criticality = Criticality.MEDIUM
    owner: str | None = None
    runbook: str | None = None
    documentation: str | None = None


class EdgeSchema(BaseModel):
    """Schema for an edge between infrastructure nodes."""

    id: str
    type: EdgeType
    from_: EdgeSourceSelector = Field(alias="from")
    to: EdgeTarget
    protocol: str | None = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    healthcheck: HealthCheckConfig = Field(default_factory=HealthCheckConfig)
    metadata: EdgeMetadata = Field(default_factory=EdgeMetadata)

    model_config = {"populate_by_name": True}


class EdgeSetSchema(BaseModel):
    """Schema for the complete edge set."""

    schema_version: str = "1.0"
    edge_types: dict[str, dict[str, Any]] = Field(default_factory=dict)
    edges: list[EdgeSchema]
