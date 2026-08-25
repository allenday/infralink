"""Strict source models for the additive observation v2 component topology."""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from infralink.observation.models import (
    CanonicalId,
    Endpoint,
    EndpointExposure,
    HostId,
    MetricCondition,
    Port,
    StrictModel,
)

_canonical_id_adapter = TypeAdapter(CanonicalId)
_host_id_adapter = TypeAdapter(HostId)

PrometheusMetricName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-zA-Z_:][a-zA-Z0-9_:]*$"),
]
PrometheusLabelName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"),
]


class EdgeScope(str, Enum):
    INTRA_SERVICE = "intra-service"
    INTER_SERVICE = "inter-service"


class ResourceKind(str, Enum):
    CONFIG = "config"
    SECRET = "secret"
    STORAGE = "storage"
    EXTERNAL_SERVICE = "external-service"


class ConfigurationValueKind(str, Enum):
    """The finite set of non-secret values a profile may request."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING_LIST = "string-list"
    STRING_LIST_MAP = "string-list-map"
    RECORD = "record"
    RECORD_LIST = "record-list"


class ConfigurationFieldKind(str, Enum):
    """The non-recursive values permitted inside declared structured configuration."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING_LIST = "string-list"
    STRING_LIST_MAP = "string-list-map"


class ArtifactKind(str, Enum):
    """The finite artifact shapes materialized by generic Operations code."""

    FILE = "file"
    TREE = "tree"


class ArtifactLifecycle(str, Enum):
    """The bounded consumer action after a declared artifact changes."""

    COMPOSE_RECREATE = "compose-recreate"
    PROVIDER_POLL = "provider-poll"


ConfigurationScalarValue = StrictStr | StrictInt | StrictBool
ConfigurationStringListMap = dict[CanonicalId, list[StrictStr]]
ConfigurationRecordValue = dict[
    CanonicalId, ConfigurationScalarValue | list[StrictStr] | ConfigurationStringListMap
]
ConfigurationValue = (
    ConfigurationScalarValue
    | list[StrictStr]
    | ConfigurationStringListMap
    | ConfigurationRecordValue
    | list[ConfigurationRecordValue]
)


class ConfigurationField(StrictModel):
    """One named non-recursive field of a declared structured configuration value."""

    id: CanonicalId
    kind: ConfigurationFieldKind
    required: bool = True


class ConfigurationSlot(StrictModel):
    """A profile-owned non-secret configuration contract, optionally component-scoped."""

    id: CanonicalId
    component_id: CanonicalId | None = None
    kind: ConfigurationValueKind
    identity_field: CanonicalId | None = None
    required: bool = True
    purpose: Annotated[str, Field(min_length=1)]
    fields: list[ConfigurationField] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_record_shape(self) -> ConfigurationSlot:
        field_ids = [field.id for field in self.fields]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("duplicate configuration record field")
        structured_kinds = {ConfigurationValueKind.RECORD, ConfigurationValueKind.RECORD_LIST}
        if self.kind in structured_kinds and not self.fields:
            raise ValueError("record configuration slot requires fields")
        if self.kind not in structured_kinds and self.fields:
            raise ValueError("configuration fields are only valid for record configuration slots")
        if self.kind is ConfigurationValueKind.RECORD_LIST:
            if self.identity_field is None:
                raise ValueError("record-list configuration slot requires identity_field")
            if self.identity_field not in field_ids:
                raise ValueError("record-list identity_field must name a declared field")
            identity_kind = next(
                field.kind for field in self.fields if field.id == self.identity_field
            )
            if identity_kind not in {
                ConfigurationFieldKind.STRING,
                ConfigurationFieldKind.INTEGER,
                ConfigurationFieldKind.BOOLEAN,
            }:
                raise ValueError("record-list identity_field must have a scalar type")
        elif self.identity_field is not None:
            raise ValueError("identity_field is only valid for record-list configuration slots")
        return self


class ConfigurationBinding(StrictModel):
    """One value bound to a declared profile configuration slot."""

    slot_id: CanonicalId
    value: ConfigurationValue


def _relative_artifact_path(value: str, *, field: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"artifact {field} must be a non-empty relative path")
    return path.as_posix()


def _artifact_targets_overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    return (
        left_parts == right_parts[: len(left_parts)]
        or right_parts == left_parts[: len(right_parts)]
    )


class ArtifactSlot(StrictModel):
    """A profile-owned, non-secret contract for one materialized artifact."""

    id: CanonicalId
    component_id: CanonicalId | None = None
    kind: ArtifactKind
    target: Annotated[str, Field(min_length=1)]
    mode: StrictInt
    owner_uid: StrictInt
    owner_gid: StrictInt
    consumer_id: CanonicalId
    lifecycle: ArtifactLifecycle
    required: bool = True
    purpose: Annotated[str, Field(min_length=1)]

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        return _relative_artifact_path(value, field="target")

    @model_validator(mode="after")
    def validate_metadata(self) -> ArtifactSlot:
        if self.mode < 0 or self.mode > 0o777:
            raise ValueError("artifact mode must be between 0 and 0777")
        if self.owner_uid < 0 or self.owner_gid < 0:
            raise ValueError("artifact ownership must be non-negative")
        return self


class ArtifactSource(StrictModel):
    """One exact registry source selected by an instance binding."""

    path: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    relative_target: Annotated[str, Field(min_length=1)] | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_artifact_path(value, field="source path")

    @field_validator("relative_target")
    @classmethod
    def validate_relative_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _relative_artifact_path(value, field="source relative_target")


class ArtifactBinding(StrictModel):
    """Instance-selected, integrity-bound Registry sources for one artifact slot."""

    slot_id: CanonicalId
    sources: list[ArtifactSource] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_sources(self) -> ArtifactBinding:
        identities = [(source.path, source.relative_target) for source in self.sources]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate artifact source binding")
        return self


class ResourceSlot(StrictModel):
    id: CanonicalId
    kind: ResourceKind
    required: bool = True
    contract_ref: CanonicalId | None = None

    @model_validator(mode="after")
    def validate_kind_contract(self) -> ResourceSlot:
        if self.kind is ResourceKind.EXTERNAL_SERVICE and self.contract_ref is None:
            raise ValueError("external-service resource slot requires contract_ref")
        if self.kind is not ResourceKind.EXTERNAL_SERVICE and self.contract_ref is not None:
            raise ValueError("contract_ref is only valid for external-service resource slots")
        return self


class ResourceBinding(StrictModel):
    resource_id: CanonicalId
    reference: Annotated[str, Field(min_length=1)]


class ExternalServiceContract(StrictModel):
    """An opaque declared external dependency; credentials remain a separate resource."""

    id: CanonicalId
    kind: CanonicalId


class SecretReference(StrictModel):
    """A value-free named secret reference resolved through a provider alias."""

    id: CanonicalId
    provider_alias_id: CanonicalId


def parse_component_endpoint_ref(value: str) -> tuple[str, str, str, str]:
    """Validate and split a canonical host/service/component/endpoint identity."""
    parts = value.split("/")
    if len(parts) != 4:
        raise ValueError("component endpoint reference must use host/service/component/endpoint")
    host_id, service_id, component_id, endpoint_id = parts
    try:
        return (
            _host_id_adapter.validate_python(host_id),
            _canonical_id_adapter.validate_python(service_id),
            _canonical_id_adapter.validate_python(component_id),
            _canonical_id_adapter.validate_python(endpoint_id),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "component endpoint reference must use host/service/component/endpoint"
        ) from error


class ComponentSlot(StrictModel):
    """A component template owned by a service profile."""

    id: CanonicalId
    endpoints: list[Endpoint] = Field(default_factory=list)
    resource_slots: list[ResourceSlot] = Field(default_factory=list)
    metrics: list[MetricContract] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_endpoint_ids(self) -> ComponentSlot:
        endpoint_ids = [endpoint.id for endpoint in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("duplicate component endpoint id")
        if any(endpoint.address is not None for endpoint in self.endpoints):
            raise ValueError("component endpoint address must be bound by a service instance")
        resource_ids = [slot.id for slot in self.resource_slots]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("duplicate component resource slot id")
        metric_ids = [metric.id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("duplicate component metric id")
        endpoint_id_set = set(endpoint_ids)
        for metric in self.metrics:
            if metric.endpoint_id not in endpoint_id_set:
                raise ValueError("component metric references an unknown endpoint")
        return self


class MetricContract(StrictModel):
    """One component-owned application metric and readiness assertion."""

    id: CanonicalId
    endpoint_id: CanonicalId
    path: str
    metric_name: PrometheusMetricName
    unit: CanonicalId
    allowed_labels: list[PrometheusLabelName] = Field(default_factory=list)
    health_query: str | None = None
    condition: MetricCondition | None = None
    readiness_required: bool = False

    @field_validator("path")
    @classmethod
    def require_absolute_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("metric scrape path must be absolute")
        return value

    @model_validator(mode="after")
    def validate_metric_health_contract(self) -> MetricContract:
        if len(self.allowed_labels) != len(set(self.allowed_labels)):
            raise ValueError("duplicate allowed metric label")
        if (self.health_query is None) != (self.condition is None):
            raise ValueError("metric health query and condition must be declared together")
        if self.readiness_required and self.health_query is None:
            raise ValueError("readiness-required metric must declare health query and condition")
        return self


class EndpointBinding(StrictModel):
    """Deployment-specific bind addresses for one component endpoint.

    ``address`` remains accepted for existing catalogs.  ``addresses`` records
    a complete multi-address bind set; its first member is the canonical
    observation target unless an instance override supplies one.
    """

    endpoint_id: CanonicalId
    address: Annotated[str, Field(min_length=1)] | None = None
    addresses: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "address": {"type": "string", "minLength": 1},
                        "addresses": {"maxItems": 0},
                    },
                    "required": ["address"],
                },
                {
                    "properties": {
                        "address": {"type": "null"},
                        "addresses": {"minItems": 1},
                    },
                    "required": ["addresses"],
                },
            ]
        },
    )

    @model_validator(mode="after")
    def validate_address_forms(self) -> EndpointBinding:
        if self.address is not None and self.addresses:
            raise ValueError("component endpoint binding accepts address or addresses, not both")
        if self.address is None and not self.addresses:
            raise ValueError("component endpoint binding requires address or addresses")
        if len(self.addresses) != len(set(self.addresses)):
            raise ValueError("duplicate component endpoint bind address")
        return self

    @property
    def canonical_address(self) -> str:
        return self.address if self.address is not None else self.addresses[0]


class ComponentEndpointOverride(StrictModel):
    """Instance-specific transport and visibility values for one profile endpoint."""

    endpoint_id: CanonicalId
    address: str | None = None
    port: Port | None = None
    exposure: EndpointExposure | None = None


class MetricBinding(StrictModel):
    """Deployment-specific labels for one declared component metric."""

    metric_id: CanonicalId
    labels: dict[PrometheusLabelName, str] = Field(default_factory=dict)


class ComponentInstance(StrictModel):
    """One realized component slot in a service instance."""

    slot_id: CanonicalId
    endpoint_bindings: list[EndpointBinding] = Field(default_factory=list)
    endpoint_overrides: list[ComponentEndpointOverride] = Field(default_factory=list)
    resource_bindings: list[ResourceBinding] = Field(default_factory=list)
    metric_bindings: list[MetricBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_metric_bindings(self) -> ComponentInstance:
        endpoint_ids = [binding.endpoint_id for binding in self.endpoint_bindings]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("duplicate component endpoint binding")
        override_endpoint_ids = [override.endpoint_id for override in self.endpoint_overrides]
        if len(override_endpoint_ids) != len(set(override_endpoint_ids)):
            raise ValueError("duplicate component endpoint override")
        resource_ids = [binding.resource_id for binding in self.resource_bindings]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("duplicate component resource binding")
        metric_ids = [binding.metric_id for binding in self.metric_bindings]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("duplicate component metric binding")
        return self


class ServiceProfileV2(StrictModel):
    """A reusable service profile composed from component slots."""

    id: CanonicalId
    components: list[ComponentSlot] = Field(min_length=1)
    configuration_slots: list[ConfigurationSlot] = Field(default_factory=list)
    artifact_slots: list[ArtifactSlot] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_component_ids(self) -> ServiceProfileV2:
        component_ids = [component.id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("duplicate component slot id")
        configuration_slot_ids = [slot.id for slot in self.configuration_slots]
        if len(configuration_slot_ids) != len(set(configuration_slot_ids)):
            raise ValueError("duplicate profile configuration slot")
        artifact_slot_ids = [slot.id for slot in self.artifact_slots]
        if len(artifact_slot_ids) != len(set(artifact_slot_ids)):
            raise ValueError("duplicate profile artifact slot")
        artifact_targets = [slot.target for slot in self.artifact_slots]
        if any(
            _artifact_targets_overlap(left, right)
            for index, left in enumerate(artifact_targets)
            for right in artifact_targets[index + 1 :]
        ):
            raise ValueError("artifact slot targets must not overlap")
        if any(
            slot.component_id is not None and slot.component_id not in component_ids
            for slot in self.configuration_slots
        ):
            raise ValueError("configuration slot references an unknown profile component")
        if any(
            slot.component_id is not None and slot.component_id not in component_ids
            for slot in self.artifact_slots
        ):
            raise ValueError("artifact slot references an unknown profile component")
        return self


class ServiceInstanceV2(StrictModel):
    """A host-local instantiation of a v2 service profile."""

    id: CanonicalId
    host_id: HostId
    profile_id: CanonicalId
    components: list[ComponentInstance] = Field(min_length=1)
    configuration_bindings: list[ConfigurationBinding] = Field(default_factory=list)
    artifact_bindings: list[ArtifactBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_slot_bindings(self) -> ServiceInstanceV2:
        slot_ids = [component.slot_id for component in self.components]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("duplicate component slot binding")
        configuration_slot_ids = [binding.slot_id for binding in self.configuration_bindings]
        if len(configuration_slot_ids) != len(set(configuration_slot_ids)):
            raise ValueError("duplicate configuration binding")
        artifact_slot_ids = [binding.slot_id for binding in self.artifact_bindings]
        if len(artifact_slot_ids) != len(set(artifact_slot_ids)):
            raise ValueError("duplicate artifact binding")
        return self


class ComponentEdge(StrictModel):
    """An edge whose ownership is derived from its component endpoint identities."""

    id: CanonicalId
    source_endpoint_id: str
    target_endpoint_id: str

    @model_validator(mode="after")
    def validate_endpoint_references(self) -> ComponentEdge:
        parse_component_endpoint_ref(self.source_endpoint_id)
        parse_component_endpoint_ref(self.target_endpoint_id)
        return self

    @property
    def source_owner(self) -> tuple[str, str, str]:
        return parse_component_endpoint_ref(self.source_endpoint_id)[:3]

    @property
    def target_owner(self) -> tuple[str, str, str]:
        return parse_component_endpoint_ref(self.target_endpoint_id)[:3]

    @property
    def scope(self) -> EdgeScope:
        source = parse_component_endpoint_ref(self.source_endpoint_id)
        target = parse_component_endpoint_ref(self.target_endpoint_id)
        return EdgeScope.INTRA_SERVICE if source[:2] == target[:2] else EdgeScope.INTER_SERVICE
