"""Deterministic normalization of loaded observation contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from infralink.observation.canonical import canonical_digest
from infralink.observation.diagnostics import Diagnostic, DiagnosticSet, SourceLocation
from infralink.observation.loader import DEFAULT_DIAGNOSTIC_LIMIT, ObservationDocument
from infralink.observation.models import (
    Application,
    DatasourceBinding,
    DependencyContract,
    Endpoint,
    EndpointExposure,
    EndpointOverride,
    EndpointProtocol,
    HealthCapability,
    Host,
    LogCapability,
    LogicalSignal,
    MetricsCapability,
    ObservationBackend,
    OperationsView,
    ProviderAlias,
    ReadinessSuite,
    RendererBindingIdentity,
    SecretBinding,
    SecretDeliveryForm,
    SecretSlot,
    ServiceInstance,
    ServiceProfile,
    SignalRequirement,
    Waiver,
    WaiverScopeKind,
)


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceRef(PlanModel):
    path: str
    document_index: int
    pointer: str


class PlannedHost(PlanModel):
    id: str
    source_refs: tuple[SourceRef, ...]


class PlannedService(PlanModel):
    id: str
    host_id: str
    instance_key: str
    profile_id: str
    source_refs: tuple[SourceRef, ...]


class PlannedServiceProfile(PlanModel):
    id: str
    endpoints: tuple[Endpoint, ...]
    health: tuple[HealthCapability, ...]
    metrics: tuple[MetricsCapability, ...]
    logs: tuple[LogCapability, ...]
    signals: tuple[LogicalSignal, ...]
    secret_slots: tuple[SecretSlot, ...]
    source_refs: tuple[SourceRef, ...]


class PlannedEndpoint(PlanModel):
    id: str
    service_id: str
    key: str
    protocol: EndpointProtocol
    port: int
    address: str | None
    exposure: EndpointExposure | None
    path: str | None
    source_refs: tuple[SourceRef, ...]


class PlannedSignal(PlanModel):
    id: str
    kind: Literal["service", "dependency", "view"]
    evaluator: str | None = None
    capability_ref: str | None = None
    service_id: str | None = None
    source_signal_ref: str | None = None
    datasource_binding_ids: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...]


class PlannedViewMember(PlanModel):
    id: str
    key: str
    signal_ref: str
    requirement: SignalRequirement
    evaluator: str
    datasource_binding_ids: tuple[str, ...] = ()
    display: str
    label: str | None
    unit: str | None
    threshold: float | None
    visualization: str | None
    visualization_class: str | None
    aggregation: str | None
    source_refs: tuple[SourceRef, ...]


class PlannedViewSection(PlanModel):
    id: str
    title: str | None
    members: tuple[PlannedViewMember, ...]
    source_refs: tuple[SourceRef, ...]


class PlannedDependency(PlanModel):
    id: str
    source_service_id: str
    target_service_id: str
    target_endpoint_id: str
    protocol: EndpointProtocol
    port: int
    required: bool
    health_signal_refs: tuple[str, ...]
    execution_adapter: str | None
    source_refs: tuple[SourceRef, ...]


class PlannedApplication(PlanModel):
    id: str
    service_ids: tuple[str, ...]
    required_dependency_ids: tuple[str, ...]
    health_signal_refs: tuple[str, ...]
    source_refs: tuple[SourceRef, ...]


class PlannedSecretRequirement(PlanModel):
    id: str
    service_id: str
    slot_id: str
    required: bool
    delivery_forms: tuple[SecretDeliveryForm, ...]
    source_refs: tuple[SourceRef, ...]


class PlannedSecretBinding(PlanModel):
    id: str
    service_id: str
    slot_id: str
    alias: str
    delivery: SecretDeliveryForm
    renderer_binding_id: str | None
    source_refs: tuple[SourceRef, ...]


class PlannedAlias(PlanModel):
    id: str
    provider: str
    project: str
    object_id: str
    source_refs: tuple[SourceRef, ...]


class PlannedWaiver(PlanModel):
    id: str
    scope_kind: WaiverScopeKind
    target_ref: str
    suite_ref: str | None
    expires_on: str
    source_refs: tuple[SourceRef, ...]


class OpaqueIdentity(PlanModel):
    id: str
    kind: str
    identity_digest: str
    source_refs: tuple[SourceRef, ...]


class PlannedOperationsView(PlanModel):
    id: str
    purpose: str
    sections: tuple[PlannedViewSection, ...]
    source_refs: tuple[SourceRef, ...]


class PlannedSuiteMember(PlanModel):
    id: str
    signal_ref: str
    cadence_seconds: int
    continuity_seconds: int
    freshness_seconds: int
    no_data_policy: Literal["fail"]
    error_policy: Literal["fail"]
    source_refs: tuple[SourceRef, ...]


class PlannedReadinessSuite(PlanModel):
    id: str
    members: tuple[PlannedSuiteMember, ...]
    suite_digest: str
    scoped_plan_digest: str
    source_refs: tuple[SourceRef, ...]


class PlanCompatibility(PlanModel):
    infralink_schema: Literal["v1"] = "v1"
    adapter_contract: Literal["v1"] = "v1"


class Plan(PlanModel):
    schema_version: Literal["infralink.plan.v1"] = "infralink.plan.v1"
    registry_revision: str | None
    document_digests: tuple[str, ...]
    compatibility: PlanCompatibility = Field(default_factory=PlanCompatibility)
    service_profiles: tuple[PlannedServiceProfile, ...]
    hosts: tuple[PlannedHost, ...]
    services: tuple[PlannedService, ...]
    endpoints: tuple[PlannedEndpoint, ...]
    applications: tuple[PlannedApplication, ...]
    dependencies: tuple[PlannedDependency, ...]
    signals: tuple[PlannedSignal, ...]
    waivers: tuple[PlannedWaiver, ...]
    secret_requirements: tuple[PlannedSecretRequirement, ...]
    secret_bindings: tuple[PlannedSecretBinding, ...]
    provider_aliases: tuple[PlannedAlias, ...]
    opaque_identities: tuple[OpaqueIdentity, ...]
    operations_views: tuple[PlannedOperationsView, ...] = ()
    readiness_suites: tuple[PlannedReadinessSuite, ...] = ()
    plan_digest: str | None = None


class PlanReport(PlanModel):
    diagnostics: DiagnosticSet


class PlanValidationError(ValueError):
    """Raised only after all independently resolvable plan findings are collected."""

    def __init__(self, report: PlanReport) -> None:
        self.report = report
        super().__init__(f"observation plan has {report.diagnostics.error_count} error(s)")


_SECTIONS: dict[str, type[BaseModel]] = {
    "service_profiles": ServiceProfile,
    "hosts": Host,
    "service_instances": ServiceInstance,
    "dependency_contracts": DependencyContract,
    "applications": Application,
    "provider_aliases": ProviderAlias,
    "secret_bindings": SecretBinding,
    "renderer_binding_identities": RendererBindingIdentity,
    "renderer_bindings": RendererBindingIdentity,
    "observation_backends": ObservationBackend,
    "datasource_bindings": DatasourceBinding,
    "waivers": Waiver,
    "operations_views": OperationsView,
    "readiness_suites": ReadinessSuite,
}
_TOP_LEVEL = {"schema_version", "registry_revision", *_SECTIONS}


def resolve_observation_documents(
    documents: Iterable[ObservationDocument],
    *,
    as_of: datetime,
    diagnostic_limit: int = DEFAULT_DIAGNOSTIC_LIMIT,
) -> Plan:
    """Resolve valid loaded documents into a sorted plan or one bounded error report."""

    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    docs = tuple(documents)
    findings: list[Diagnostic] = []
    if not docs:
        findings.append(
            Diagnostic(
                code="no-usable-observation-document",
                severity="error",
                message="At least one usable observation document is required.",
                location=SourceLocation("<input>"),
                identity="observation-documents",
                next_actions=(
                    "Supply an infralink.observation/v1 document with contract records.",
                ),
            )
        )
    parsed: dict[str, list[tuple[BaseModel, SourceRef]]] = {key: [] for key in _SECTIONS}
    revisions: set[str] = set()
    usable_documents = 0
    for doc in docs:
        data = doc.to_dict()
        if data.get("schema_version") != "infralink.observation/v1":
            _add(
                findings,
                "schema-version-unsupported",
                doc,
                "/schema_version",
                "schema_version",
                "Set schema_version to infralink.observation/v1.",
            )
        elif any(data.get(section) for section in _SECTIONS):
            usable_documents += 1
        for key in sorted(set(data) - _TOP_LEVEL):
            _add(
                findings,
                "unknown-document-field",
                doc,
                f"/{_escape(key)}",
                key,
                "Remove the unsupported top-level field.",
            )
        revision = data.get("registry_revision")
        if revision is not None:
            if isinstance(revision, str) and revision:
                revisions.add(revision)
            else:
                _add(
                    findings,
                    "invalid-document-section",
                    doc,
                    "/registry_revision",
                    "registry_revision",
                    "Use a non-empty string registry revision.",
                )
        for section, model in _SECTIONS.items():
            raw = data.get(section, [])
            if not isinstance(raw, list):
                _add(
                    findings,
                    "invalid-document-section",
                    doc,
                    f"/{section}",
                    section,
                    "Replace the section with a list of typed records.",
                )
                continue
            for index, value in enumerate(raw):
                pointer = f"/{section}/{index}"
                try:
                    item = model.model_validate_json(json.dumps(value))
                except ValidationError as error:
                    for detail in error.errors(include_url=False):
                        suffix = "".join(f"/{_escape(str(part))}" for part in detail["loc"])
                        identity = value.get("id") if isinstance(value, dict) else section
                        _add(
                            findings,
                            "invalid-document-record",
                            doc,
                            pointer + suffix,
                            str(identity),
                            "Correct the record to match the closed section schema.",
                        )
                    continue
                except (TypeError, ValueError, RecursionError):
                    identity = value.get("id") if isinstance(value, dict) else section
                    _add(
                        findings,
                        "invalid-document-record",
                        doc,
                        pointer,
                        str(identity),
                        "Replace non-JSON or circular values with supported contract values.",
                    )
                    continue
                parsed[section].append((item, _ref(doc, pointer)))

    if docs and usable_documents == 0:
        _add(
            findings,
            "no-usable-observation-document",
            docs[0],
            "/",
            "observation-documents",
            "Supply a versioned document with at least one supported contract record.",
        )

    if len(revisions) > 1:
        first = docs[0]
        _add(
            findings,
            "registry-revision-conflict",
            first,
            "/registry_revision",
            "registry_revision",
            "Use one registry revision across all documents.",
        )

    profiles = _unique(parsed["service_profiles"], "profile", findings)
    hosts = _unique(parsed["hosts"], "host", findings)
    instance_entries = parsed["service_instances"]
    aliases = _unique(parsed["provider_aliases"], "provider-alias", findings)
    bindings = _unique(parsed["secret_bindings"], "secret-binding", findings)
    dependencies = _unique(parsed["dependency_contracts"], "dependency", findings)
    applications = _unique(parsed["applications"], "application", findings)
    waivers = _unique(parsed["waivers"], "waiver", findings)
    operation_views = _unique(parsed["operations_views"], "view", findings)
    readiness_suites = _unique(parsed["readiness_suites"], "suite", findings)
    backends = _unique(parsed["observation_backends"], "observation-backend", findings)
    datasources = _unique(parsed["datasource_bindings"], "datasource-binding", findings)
    for datasource, datasource_ref in datasources.values():
        assert isinstance(datasource, DatasourceBinding)
        if datasource.backend_id not in backends:
            _finding(
                findings,
                "unknown-observation-backend",
                _child(datasource_ref, "backend_id"),
                datasource.id,
                "Reference a declared observation backend.",
            )
    planned_profiles = [
        PlannedServiceProfile(
            id=profile.id,
            endpoints=tuple(sorted(profile.endpoints, key=lambda item: item.id)),
            health=tuple(sorted(profile.health, key=lambda item: item.id)),
            metrics=tuple(sorted(profile.metrics, key=lambda item: item.id)),
            logs=tuple(sorted(profile.logs, key=lambda item: item.id)),
            signals=tuple(sorted(profile.signals, key=lambda item: item.id)),
            secret_slots=tuple(sorted(profile.secret_slots, key=lambda item: item.id)),
            source_refs=(ref,),
        )
        for profile, ref in profiles.values()
        if isinstance(profile, ServiceProfile)
    ]

    planned_hosts: list[PlannedHost] = []
    for host, ref in hosts.values():
        assert isinstance(host, Host)
        planned_hosts.append(PlannedHost(id=str(host.id), source_refs=(ref,)))
    host_ids = {item.id for item in planned_hosts}
    planned_services: list[PlannedService] = []
    planned_endpoints: list[PlannedEndpoint] = []
    planned_signals: list[PlannedSignal] = []
    requirements: list[PlannedSecretRequirement] = []
    service_profiles: dict[str, ServiceProfile] = {}
    service_refs: dict[str, SourceRef] = {}
    instance_by_service: dict[str, ServiceInstance] = {}
    seen_service_ids: set[str] = set()

    for instance, ref in instance_entries:
        assert isinstance(instance, ServiceInstance)
        if instance.host_id is None:
            _finding(
                findings,
                "missing-service-host",
                _child(ref, "host_id"),
                instance.id,
                "Set host_id to an existing UUID host identity.",
            )
            continue
        host_id = str(instance.host_id)
        service_id = f"{host_id}/{instance.id}"
        if service_id in seen_service_ids:
            _finding(
                findings,
                "duplicate-service-id",
                _child(ref, "id"),
                service_id,
                "Give each service instance key one identity per host.",
            )
            continue
        seen_service_ids.add(service_id)
        profile_entry = profiles.get(instance.profile_id)
        if profile_entry is None:
            _finding(
                findings,
                "unknown-profile",
                _child(ref, "profile_id"),
                service_id,
                "Set profile_id to a declared service profile.",
            )
            continue
        if host_id not in host_ids:
            _finding(
                findings,
                "unknown-host",
                _child(ref, "host_id"),
                service_id,
                "Set host_id to a declared host UUID.",
            )
            continue
        profile = profile_entry[0]
        assert isinstance(profile, ServiceProfile)
        planned_services.append(
            PlannedService(
                id=service_id,
                host_id=host_id,
                instance_key=instance.id,
                profile_id=profile.id,
                source_refs=(ref, profile_entry[1]),
            )
        )
        service_profiles[service_id] = profile
        service_refs[service_id] = ref
        instance_by_service[service_id] = instance
        profile_endpoint_ids = {endpoint.id for endpoint in profile.endpoints}
        selected = set(instance.endpoint_ids) if instance.endpoint_ids else profile_endpoint_ids
        for selection_index, endpoint_id in enumerate(instance.endpoint_ids):
            if endpoint_id not in profile_endpoint_ids:
                _finding(
                    findings,
                    "unknown-selected-endpoint",
                    _child(ref, "endpoint_ids", str(selection_index)),
                    f"{service_id}/{endpoint_id}",
                    "Select only an endpoint declared by the service profile.",
                )
        overrides: dict[str, tuple[EndpointOverride, SourceRef]] = {}
        for override_index, override in enumerate(instance.endpoint_overrides):
            override_ref = _child(ref, "endpoint_overrides", str(override_index))
            if override.endpoint_id in overrides:
                _finding(
                    findings,
                    "duplicate-endpoint-override",
                    _child(override_ref, "endpoint_id"),
                    f"{service_id}/{override.endpoint_id}",
                    "Declare at most one override for each service endpoint.",
                )
                continue
            overrides[override.endpoint_id] = (override, override_ref)
            if override.endpoint_id not in profile_endpoint_ids:
                _finding(
                    findings,
                    "unknown-endpoint-override",
                    _child(override_ref, "endpoint_id"),
                    f"{service_id}/{override.endpoint_id}",
                    "Override only an endpoint declared by the service profile.",
                )
            elif override.endpoint_id not in selected:
                _finding(
                    findings,
                    "endpoint-override-not-selected",
                    _child(override_ref, "endpoint_id"),
                    f"{service_id}/{override.endpoint_id}",
                    "Add the endpoint to endpoint_ids before declaring its override.",
                )
        for endpoint_index, endpoint in enumerate(profile.endpoints):
            if endpoint.id not in selected:
                continue
            override_entry = overrides.get(endpoint.id)
            resolved_override = override_entry[0] if override_entry is not None else None
            planned_endpoints.append(
                PlannedEndpoint(
                    id=f"{service_id}/{endpoint.id}",
                    service_id=service_id,
                    key=endpoint.id,
                    protocol=endpoint.protocol,
                    port=endpoint.port,
                    address=(
                        resolved_override.address
                        if resolved_override is not None and resolved_override.address is not None
                        else endpoint.address
                    ),
                    exposure=(
                        resolved_override.exposure if resolved_override is not None else None
                    ),
                    path=(
                        resolved_override.route
                        if resolved_override is not None and resolved_override.route is not None
                        else endpoint.path
                    ),
                    source_refs=(
                        ref,
                        _child(profile_entry[1], "endpoints", str(endpoint_index)),
                    )
                    + ((override_entry[1],) if override_entry is not None else ()),
                )
            )
        for signal_index, signal in enumerate(profile.signals):
            signal_id = f"service/{service_id}/{signal.capability_id}/{signal.id}"
            planned_signals.append(
                PlannedSignal(
                    id=signal_id,
                    kind="service",
                    evaluator=signal.evaluator.value,
                    capability_ref=f"service/{service_id}/{signal.capability_id}",
                    service_id=service_id,
                    source_refs=(_child(profile_entry[1], "signals", str(signal_index)), ref),
                )
            )
        for slot_index, slot in enumerate(profile.secret_slots):
            requirements.append(
                PlannedSecretRequirement(
                    id=f"{service_id}/{slot.id}",
                    service_id=service_id,
                    slot_id=slot.id,
                    required=slot.required,
                    delivery_forms=tuple(slot.delivery_forms),
                    source_refs=(_child(profile_entry[1], "secret_slots", str(slot_index)), ref),
                )
            )

    endpoint_map = _index_planned(planned_endpoints, "endpoint", findings)
    signal_map = _index_planned(planned_signals, "signal", findings)
    service_ids = {service.id for service in planned_services}
    planned_dependencies: list[PlannedDependency] = []
    for edge, ref in dependencies.values():
        assert isinstance(edge, DependencyContract)
        target = endpoint_map.get(edge.target_endpoint_id)
        if edge.source_service_id not in service_ids:
            _finding(
                findings,
                "unknown-dependency-source",
                _child(ref, "source_service_id"),
                edge.id,
                "Set source_service_id to a canonical existing service ID.",
            )
        if edge.target_service_id not in service_ids:
            _finding(
                findings,
                "unknown-dependency-target",
                _child(ref, "target_service_id"),
                edge.id,
                "Set target_service_id to a canonical existing service ID.",
            )
        if target is None:
            _finding(
                findings,
                "unknown-endpoint",
                _child(ref, "target_endpoint_id"),
                edge.id,
                "Set target_endpoint_id to a canonical existing endpoint ID.",
            )
            continue
        if target.service_id != edge.target_service_id:
            _finding(
                findings,
                "dependency-target-mismatch",
                _child(ref, "target_endpoint_id"),
                edge.id,
                "Choose an endpoint owned by target_service_id.",
            )
        if edge.protocol is None or edge.protocol != target.protocol:
            _finding(
                findings,
                "dependency-protocol-conflict",
                _child(ref, "protocol"),
                edge.id,
                "Match protocol to the target endpoint protocol.",
            )
        if edge.port is None or edge.port != target.port:
            _finding(
                findings,
                "dependency-port-conflict",
                _child(ref, "port"),
                edge.id,
                "Match port to the target endpoint listener port.",
            )
        health_refs = (edge.health_signal_ref,)
        expected_pattern = (
            rf"dependency/{re.escape(edge.id)}/health/[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
        )
        if re.fullmatch(expected_pattern, edge.health_signal_ref) is None:
            _finding(
                findings,
                "invalid-dependency-health-signal-ref",
                _child(ref, "health_signal_ref"),
                edge.id,
                "Use dependency/<edge-id>/health/<signal-key> for the health signal reference.",
            )
        for signal_id in health_refs:
            planned_signal = PlannedSignal(
                id=signal_id,
                kind="dependency",
                source_refs=(_child(ref, "health_signal_ref"),),
            )
            if signal_id in signal_map:
                _finding(
                    findings,
                    "duplicate-signal-id",
                    ref,
                    signal_id,
                    "Give every dependency health signal a unique identity.",
                )
            else:
                signal_map[signal_id] = planned_signal
                planned_signals.append(planned_signal)
        planned_dependencies.append(
            PlannedDependency(
                id=edge.id,
                source_service_id=edge.source_service_id,
                target_service_id=edge.target_service_id,
                target_endpoint_id=target.id,
                protocol=edge.protocol,
                port=edge.port,
                required=edge.required,
                health_signal_refs=health_refs,
                execution_adapter=edge.execution_adapter,
                source_refs=(ref,),
            )
        )

    renderer_entries = _unique(
        [*parsed["renderer_binding_identities"], *parsed["renderer_bindings"]],
        "renderer-binding",
        findings,
    )
    planned_bindings: list[PlannedSecretBinding] = []
    planned_binding_ids: set[str] = set()
    for service in planned_services:
        instance = instance_by_service[service.id]
        profile = service_profiles[service.id]
        slots = {slot.id: slot for slot in profile.secret_slots}
        for binding_index, binding_id in enumerate(instance.secret_binding_ids):
            entry = bindings.get(binding_id)
            if entry is None:
                _finding(
                    findings,
                    "unknown-secret-binding",
                    _child(service_refs[service.id], "secret_binding_ids", str(binding_index)),
                    binding_id,
                    "Declare the referenced secret binding.",
                )
                continue
            binding, ref = entry
            assert isinstance(binding, SecretBinding)
            selected_slot = slots.get(binding.slot_id)
            if selected_slot is None:
                _finding(
                    findings,
                    "unknown-secret-slot",
                    _child(ref, "slot_id"),
                    binding.id,
                    "Bind only a slot declared by the service profile.",
                )
                continue
            if binding.alias not in aliases:
                _finding(
                    findings,
                    "unknown-provider-alias",
                    _child(ref, "alias"),
                    binding.id,
                    "Declare the logical provider alias in the global catalog.",
                )
            if binding.delivery is None or binding.delivery not in selected_slot.delivery_forms:
                _finding(
                    findings,
                    "secret-delivery-incompatible",
                    _child(ref, "delivery"),
                    binding.id,
                    "Choose a delivery form allowed by the profile secret slot.",
                )
                continue
            if binding.renderer_binding_id is not None:
                renderer = renderer_entries.get(binding.renderer_binding_id)
                if renderer is None:
                    _finding(
                        findings,
                        "unknown-renderer-binding",
                        _child(ref, "renderer_binding_id"),
                        binding.id,
                        "Reference a declared renderer binding identity.",
                    )
                else:
                    identity = renderer[0]
                    assert isinstance(identity, RendererBindingIdentity)
                    if identity.delivery_forms and binding.delivery not in identity.delivery_forms:
                        _finding(
                            findings,
                            "renderer-delivery-incompatible",
                            _child(ref, "delivery"),
                            binding.id,
                            "Choose a delivery form supported by the renderer binding.",
                        )
            planned_binding_id = f"{service.id}/{binding.slot_id}"
            if planned_binding_id in planned_binding_ids:
                _finding(
                    findings,
                    "duplicate-secret-binding-id",
                    _child(ref, "slot_id"),
                    planned_binding_id,
                    "Bind each service secret slot at most once.",
                )
                continue
            planned_binding_ids.add(planned_binding_id)
            planned_bindings.append(
                PlannedSecretBinding(
                    id=planned_binding_id,
                    service_id=service.id,
                    slot_id=binding.slot_id,
                    alias=binding.alias,
                    delivery=binding.delivery,
                    renderer_binding_id=binding.renderer_binding_id,
                    source_refs=(ref,),
                )
            )
        bound_slots = {b.slot_id for b in planned_bindings if b.service_id == service.id}
        profile_ref = profiles[profile.id][1]
        for slot_index, slot in enumerate(profile.secret_slots):
            if slot.required and slot.id not in bound_slots:
                _finding(
                    findings,
                    "required-secret-slot-unbound",
                    _child(profile_ref, "secret_slots", str(slot_index)),
                    f"{service.id}/{slot.id}",
                    "Bind the required slot to a declared provider alias.",
                )

    dependency_ids = {edge.id for edge in planned_dependencies}
    all_signal_ids = {signal.id for signal in planned_signals}
    planned_apps: list[PlannedApplication] = []
    for app, ref in applications.values():
        assert isinstance(app, Application)
        for service_index, service_id in enumerate(app.service_instance_ids):
            if service_id not in service_ids:
                _finding(
                    findings,
                    "unknown-application-service",
                    _child(ref, "service_instance_ids", str(service_index)),
                    app.id,
                    "Use the canonical host-UUID/service-instance-key identity.",
                )
        for edge_index, edge_id in enumerate(app.required_dependency_edge_ids):
            if edge_id not in dependency_ids:
                _finding(
                    findings,
                    "unknown-application-dependency",
                    _child(ref, "required_dependency_edge_ids", str(edge_index)),
                    app.id,
                    "Reference a declared dependency edge ID.",
                )
        for signal_index, signal_id in enumerate(app.health_signal_refs):
            if signal_id not in all_signal_ids:
                _finding(
                    findings,
                    "unknown-application-health-signal",
                    _child(ref, "health_signal_refs", str(signal_index)),
                    app.id,
                    "Reference a fully-qualified declared signal ID.",
                )
        planned_apps.append(
            PlannedApplication(
                id=app.id,
                service_ids=tuple(sorted(app.service_instance_ids)),
                required_dependency_ids=tuple(sorted(app.required_dependency_edge_ids)),
                health_signal_refs=tuple(sorted(app.health_signal_refs)),
                source_refs=(ref,),
            )
        )

    planned_views: list[PlannedOperationsView] = []
    view_member_requirement: dict[str, SignalRequirement] = {}
    datasource_ids = tuple(sorted(datasources))
    for view, ref in operation_views.values():
        assert isinstance(view, OperationsView)
        sections: list[PlannedViewSection] = []
        seen_section_ids: set[str] = set()
        seen_query_ids: set[str] = set()
        for section_index, view_section in enumerate(view.sections):
            section_ref = _child(ref, "sections", str(section_index))
            if view_section.id in seen_section_ids:
                _finding(
                    findings,
                    "duplicate-view-section-id",
                    _child(section_ref, "id"),
                    f"{view.id}/{view_section.id}",
                    "Give every section in a view a unique identity.",
                )
            seen_section_ids.add(view_section.id)
            view_members: list[PlannedViewMember] = []
            for member_index, member in enumerate(view_section.members):
                member_ref = _child(section_ref, "members", str(member_index))
                query_key = member.signal_id or member.id
                assert query_key is not None
                source_signal = signal_map.get(member.signal_ref)
                derived_id = f"view/{view.id}/query/{view_section.id}/{query_key}"
                if derived_id in seen_query_ids:
                    _finding(
                        findings,
                        "duplicate-view-query-id",
                        _child(member_ref, "signal_id" if member.signal_id is not None else "id"),
                        derived_id,
                        "Give every query in a view section a unique identity.",
                    )
                    continue
                seen_query_ids.add(derived_id)
                if source_signal is None:
                    _finding(
                        findings,
                        "unknown-view-signal",
                        _child(member_ref, "signal_ref"),
                        derived_id,
                        "Reference an existing service or dependency signal.",
                    )
                    continue
                resolved = PlannedViewMember(
                    id=derived_id,
                    key=query_key,
                    signal_ref=member.signal_ref,
                    requirement=member.requirement,
                    evaluator=source_signal.evaluator or "dependency-health",
                    datasource_binding_ids=source_signal.datasource_binding_ids or datasource_ids,
                    display=member.display.value,
                    label=member.label,
                    unit=member.unit,
                    threshold=member.threshold,
                    visualization=member.visualization,
                    visualization_class=member.visualization_class,
                    aggregation=member.aggregation,
                    source_refs=(member_ref, *source_signal.source_refs),
                )
                view_members.append(resolved)
                view_member_requirement[derived_id] = member.requirement
                view_signal = PlannedSignal(
                    id=derived_id,
                    kind="view",
                    evaluator=resolved.evaluator,
                    capability_ref=source_signal.capability_ref,
                    service_id=source_signal.service_id,
                    source_signal_ref=member.signal_ref,
                    datasource_binding_ids=resolved.datasource_binding_ids,
                    source_refs=resolved.source_refs,
                )
                if derived_id in signal_map:
                    _finding(
                        findings,
                        "duplicate-signal-id",
                        member_ref,
                        derived_id,
                        "Give every view query a globally unique derived identity.",
                    )
                else:
                    signal_map[derived_id] = view_signal
                    planned_signals.append(view_signal)
            sections.append(
                PlannedViewSection(
                    id=view_section.id,
                    title=view_section.title,
                    members=tuple(view_members),
                    source_refs=(section_ref,),
                )
            )
        planned_views.append(
            PlannedOperationsView(
                id=view.id,
                purpose=view.purpose or view.title or view.id,
                sections=tuple(sections),
                source_refs=(ref,),
            )
        )

    all_signal_ids = set(signal_map)
    planned_suites: list[PlannedReadinessSuite] = []
    suite_member_refs: set[tuple[str, str]] = set()
    for suite, ref in readiness_suites.values():
        assert isinstance(suite, ReadinessSuite)
        suite_members: list[PlannedSuiteMember] = []
        seen_suite_member_ids: set[str] = set()
        for member_index, suite_member in enumerate(suite.members):
            member_ref = _child(ref, "members", str(member_index))
            member_id = suite_member.id or suite_member.signal_ref.rsplit("/", 1)[-1]
            if member_id in seen_suite_member_ids:
                _finding(
                    findings,
                    "duplicate-suite-member-id",
                    _child(member_ref, "id"),
                    f"{suite.id}/{member_id}",
                    "Give every readiness suite member a unique identity.",
                )
                continue
            seen_suite_member_ids.add(member_id)
            if suite_member.signal_ref not in all_signal_ids:
                _finding(
                    findings,
                    "unknown-suite-signal",
                    _child(member_ref, "signal_ref"),
                    f"{suite.id}/{member_id}",
                    "Reference an existing service, dependency, or view query signal.",
                )
                continue
            if view_member_requirement.get(suite_member.signal_ref) == SignalRequirement.OPTIONAL:
                _finding(
                    findings,
                    "optional-view-signal-gate",
                    _child(member_ref, "signal_ref"),
                    f"{suite.id}/{member_id}",
                    "Reference a required view query or its underlying source signal.",
                )
            planned_member = PlannedSuiteMember(
                id=member_id,
                signal_ref=suite_member.signal_ref,
                cadence_seconds=suite_member.cadence_seconds,
                continuity_seconds=suite_member.continuity_seconds,
                freshness_seconds=suite_member.freshness_seconds,
                no_data_policy="fail",
                error_policy="fail",
                source_refs=(member_ref,),
            )
            suite_members.append(planned_member)
            suite_member_refs.add((suite.id, member_id))
        definition = {
            "id": suite.id,
            "members": [_semantic_value(member) for member in suite_members],
        }
        planned_suites.append(
            PlannedReadinessSuite(
                id=suite.id,
                members=tuple(suite_members),
                suite_digest=canonical_digest(definition),
                scoped_plan_digest="",
                source_refs=(ref,),
            )
        )

    capability_refs: set[str] = set()
    for service_id, profile in service_profiles.items():
        capability_refs.update(f"service/{service_id}/{item.id}" for item in profile.health)
        capability_refs.update(f"service/{service_id}/{item.id}" for item in profile.metrics)
        capability_refs.update(f"service/{service_id}/{item.id}" for item in profile.logs)
    planned_waivers: list[PlannedWaiver] = []
    for waiver, ref in waivers.values():
        assert isinstance(waiver, Waiver)
        if waiver.expires_on <= as_of.date():
            _finding(
                findings,
                "waiver-expired",
                _child(ref, "expires_on"),
                waiver.id,
                "Remove the waiver or replace it with a newly reviewed waiver.",
            )
        exists = (
            waiver.scope.ref in all_signal_ids
            if waiver.scope.kind == WaiverScopeKind.SIGNAL
            else waiver.scope.ref in capability_refs
        )
        if waiver.scope.kind == WaiverScopeKind.SUITE_MEMBER:
            exists = (waiver.scope.suite_ref or "", waiver.scope.ref) in suite_member_refs
        if not exists:
            _finding(
                findings,
                "unknown-waiver-target",
                _child(ref, "scope", "ref"),
                waiver.id,
                "Reference an existing target of the declared waiver scope kind.",
            )
        planned_waivers.append(
            PlannedWaiver(
                id=waiver.id,
                scope_kind=waiver.scope.kind,
                target_ref=waiver.scope.ref,
                suite_ref=waiver.scope.suite_ref,
                expires_on=waiver.expires_on.isoformat(),
                source_refs=(ref,),
            )
        )

    if findings:
        raise PlanValidationError(
            PlanReport(diagnostics=DiagnosticSet.from_diagnostics(findings, limit=diagnostic_limit))
        )

    opaque: list[OpaqueIdentity] = []
    for section in (
        "renderer_binding_identities",
        "renderer_bindings",
        "observation_backends",
        "datasource_bindings",
    ):
        for item, ref in parsed[section]:
            opaque.append(
                OpaqueIdentity(
                    id=str(item.id),  # type: ignore[attr-defined]
                    kind=section,
                    identity_digest=canonical_digest(_semantic_value(item)),
                    source_refs=(ref,),
                )
            )
    planned_aliases = [
        PlannedAlias(
            id=item.id,
            provider=item.provider,
            project=item.project,
            object_id=item.object_id,
            source_refs=(ref,),
        )
        for item, ref in aliases.values()
        if isinstance(item, ProviderAlias)
    ]
    plan = Plan(
        registry_revision=next(iter(revisions), None),
        document_digests=tuple(sorted(doc.semantic_sha256 for doc in docs)),
        service_profiles=_sorted(planned_profiles),
        hosts=_sorted(planned_hosts),
        services=_sorted(planned_services),
        endpoints=_sorted(planned_endpoints),
        applications=_sorted(planned_apps),
        dependencies=_sorted(planned_dependencies),
        signals=_sorted(planned_signals),
        waivers=_sorted(planned_waivers),
        secret_requirements=_sorted(requirements),
        secret_bindings=_sorted(planned_bindings),
        provider_aliases=_sorted(planned_aliases),
        opaque_identities=tuple(sorted(opaque, key=lambda item: (item.kind, item.id))),
        operations_views=_sorted(planned_views),
        readiness_suites=_sorted(planned_suites),
    )
    digest = canonical_digest(_semantic_value(plan, omit={"plan_digest", "scoped_plan_digest"}))
    scoped_suites = tuple(
        suite.model_copy(update={"scoped_plan_digest": _scoped_digest(plan, suite)})
        for suite in plan.readiness_suites
    )
    return plan.model_copy(update={"readiness_suites": scoped_suites, "plan_digest": digest})


def _unique(
    entries: list[tuple[BaseModel, SourceRef]], kind: str, findings: list[Diagnostic]
) -> dict[str, tuple[BaseModel, SourceRef]]:
    result: dict[str, tuple[BaseModel, SourceRef]] = {}
    for item, ref in entries:
        identity = str(item.id)  # type: ignore[attr-defined]
        if identity in result:
            _finding(
                findings,
                f"duplicate-{kind}-id",
                ref,
                identity,
                f"Give every {kind} a unique identity.",
            )
        else:
            result[identity] = (item, ref)
    return result


def _index_planned(items: list[Any], kind: str, findings: list[Diagnostic]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if item.id in result:
            _finding(
                findings,
                f"duplicate-{kind}-id",
                item.source_refs[0],
                item.id,
                f"Give every {kind} a unique identity.",
            )
        else:
            result[item.id] = item
    return result


def _sorted(items: list[Any]) -> tuple[Any, ...]:
    return tuple(sorted(items, key=lambda item: item.id))


def _semantic_value(value: object, *, omit: set[str] | None = None) -> object:
    """Strip diagnostic provenance and digest slots from a model tree."""

    omitted = {"source_refs", *(omit or set())}
    if isinstance(value, BaseModel):
        return {
            key: _semantic_value(child, omit=omitted)
            for key, child in value.__iter__()
            if key not in omitted and child is not None
        }
    if isinstance(value, dict):
        return {
            str(key): _semantic_value(child, omit=omitted)
            for key, child in value.items()
            if str(key) not in omitted and child is not None
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_value(child, omit=omitted) for child in value]
    return value


def _scoped_digest(plan: Plan, suite: PlannedReadinessSuite) -> str:
    signal_index = {signal.id: signal for signal in plan.signals}
    selected_signals: dict[str, PlannedSignal] = {}

    def add_signal(signal_id: str) -> None:
        signal = signal_index.get(signal_id)
        if signal is None or signal_id in selected_signals:
            return
        selected_signals[signal_id] = signal
        if signal.source_signal_ref is not None:
            add_signal(signal.source_signal_ref)

    for member in suite.members:
        add_signal(member.signal_ref)
    service_ids = {signal.service_id for signal in selected_signals.values() if signal.service_id}
    dependency_ids = {
        signal_id.split("/", 2)[1]
        for signal_id in selected_signals
        if signal_id.startswith("dependency/")
    }
    services = [service for service in plan.services if service.id in service_ids]
    profile_ids = {service.profile_id for service in services}
    capability_refs = {
        signal.capability_ref
        for signal in selected_signals.values()
        if signal.capability_ref is not None
    }
    view_ids = {
        signal_id.split("/", 3)[1]
        for signal_id in selected_signals
        if signal_id.startswith("view/")
    }
    relevant_bindings = [
        binding for binding in plan.secret_bindings if binding.service_id in service_ids
    ]
    renderer_ids = {
        binding.renderer_binding_id
        for binding in relevant_bindings
        if binding.renderer_binding_id is not None
    }
    datasource_ids = {
        datasource_id
        for signal in selected_signals.values()
        for datasource_id in signal.datasource_binding_ids
    }
    opaque = [
        identity
        for identity in plan.opaque_identities
        if (
            identity.id in renderer_ids
            or identity.id in datasource_ids
            or identity.kind == "observation_backends"
        )
    ]
    scoped = {
        "suite_id": suite.id,
        "signals": _sorted(list(selected_signals.values())),
        "services": _sorted(services),
        "hosts": _sorted([host for host in plan.hosts if host.id in {s.host_id for s in services}]),
        "profiles": _sorted(
            [profile for profile in plan.service_profiles if profile.id in profile_ids]
        ),
        "endpoints": _sorted(
            [endpoint for endpoint in plan.endpoints if endpoint.service_id in service_ids]
        ),
        "dependencies": _sorted([edge for edge in plan.dependencies if edge.id in dependency_ids]),
        "views": _sorted([view for view in plan.operations_views if view.id in view_ids]),
        "waivers": _sorted(
            [
                waiver
                for waiver in plan.waivers
                if waiver.target_ref in selected_signals
                or waiver.target_ref in capability_refs
                or waiver.suite_ref == suite.id
            ]
        ),
        "secret_bindings": _sorted(relevant_bindings),
        "provider_aliases": _sorted(
            [
                alias
                for alias in plan.provider_aliases
                if alias.id in {binding.alias for binding in relevant_bindings}
            ]
        ),
        "opaque_identities": sorted(opaque, key=lambda item: (item.kind, item.id)),
    }
    return canonical_digest(_semantic_value(scoped))


def _ref(doc: ObservationDocument, pointer: str) -> SourceRef:
    return SourceRef(path=doc.source_path, document_index=doc.document_index, pointer=pointer)


def _add(
    findings: list[Diagnostic],
    code: str,
    doc: ObservationDocument,
    pointer: str,
    identity: str,
    action: str,
) -> None:
    _finding(findings, code, _ref(doc, pointer), identity, action)


def _finding(
    findings: list[Diagnostic], code: str, ref: SourceRef, identity: str, action: str
) -> None:
    findings.append(
        Diagnostic(
            code=code,
            severity="error",
            message=code.replace("-", " ").capitalize() + ".",
            location=SourceLocation(ref.path, ref.pointer, ref.document_index),
            identity=str(identity),
            next_actions=(action,),
        )
    )


def _child(ref: SourceRef, *parts: str) -> SourceRef:
    suffix = "".join(f"/{_escape(part)}" for part in parts)
    return SourceRef(
        path=ref.path,
        document_index=ref.document_index,
        pointer=ref.pointer.rstrip("/") + suffix,
    )


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


__all__ = [
    "Plan",
    "PlanReport",
    "PlanValidationError",
    "SourceRef",
    "resolve_observation_documents",
]
