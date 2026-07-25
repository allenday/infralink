from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from infralink.cli.contracts import (
    Action,
    AnalyzeResult,
    AppListResult,
    AppShowResult,
    AppSummary,
    Artifact,
    ArtifactResult,
    Binding,
    CheckCommandResult,
    CheckResult,
    CommandContext,
    ContractModel,
    Diagnostic,
    EdgeListResult,
    EdgeShowResult,
    EdgeSummary,
    Endpoint,
    Envelope,
    ErrorDetail,
    HelpResult,
    HostListResult,
    HostShowResult,
    HostSummary,
    InfoResult,
    Page,
    PageInfo,
    ResolveResult,
    RootResult,
    SecretReferenceStatus,
    SecretsAuditResult,
    SecretsInspectResult,
    ServiceListResult,
    ServiceShowResult,
    ServiceSummary,
    SourceLocation,
    ValidateResult,
    VersionResult,
)

ROOT = Path(__file__).parents[1]
SCHEMA_NAMES = (
    "root",
    "help",
    "version",
    "info",
    "hosts",
    "host-show",
    "services",
    "service-show",
    "edges-list",
    "edge-show",
    "validate",
    "resolve",
    "check",
    "app-list",
    "app-show",
    "analyze",
    "diagram",
    "docs",
    "secrets-inspect",
    "secrets-audit",
)


def context() -> CommandContext:
    return CommandContext(
        raw="infralink validate",
        parsed={"path": ["validate"], "args": {}, "flags": []},
        resolved={"version": "0.2.0", "cwd": "/work"},
    )


def page(items: list[Any]) -> Page[Any]:
    return Page(items=items, page=PageInfo(limit=100, returned=len(items), total=len(items)))


def host() -> HostSummary:
    return HostSummary(
        id="host-1",
        canonical_name="host.example",
        status="active",
        service_count=1,
        services=["api"],
        services_truncated=False,
        project_count=1,
        projects=["core"],
        projects_truncated=False,
    )


def service() -> ServiceSummary:
    return ServiceSummary(
        id="api",
        host_count=1,
        host_ids=["host-1"],
        hosts_truncated=False,
        port_count=1,
        ports=[443],
        ports_truncated=False,
        protocol_count=1,
        protocols=["https"],
        protocols_truncated=False,
    )


def edge() -> EdgeSummary:
    return EdgeSummary(
        id="edge-1",
        type="http",
        from_={"host": "host-1"},
        to={"host": "host-2"},
        protocol="https",
        secret_ref_count=1,
        secret_refs=["secret://api"],
        secret_refs_truncated=False,
    )


def secret() -> SecretReferenceStatus:
    return SecretReferenceStatus(
        ref="secret://api",
        location_count=1,
        location_preview=[SourceLocation(source="edges", path="edges.edge-1.secret_ref")],
        locations_truncated=False,
        project="core",
        present=True,
        accessible=True,
        error_code=None,
    )


def test_contract_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PageInfo(limit=1, returned=0, unexpected=True)
    assert issubclass(PageInfo, ContractModel)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("limit", 0),
        ("limit", 1001),
        ("returned", -1),
        ("total", -1),
    ],
)
def test_page_info_bounds(field: str, value: int) -> None:
    values = {"limit": 100, "returned": 0, "total": 0}
    values[field] = value
    with pytest.raises(ValidationError):
        PageInfo(**values)


def test_validate_envelope_is_typed_and_serializable() -> None:
    payload = Envelope[ValidateResult](
        ok=True,
        command=context(),
        result=ValidateResult(
            valid=True,
            errors=page([]),
            warnings=page([]),
            summary={"error_count": 0, "warning_count": 0},
        ),
        next_actions=[
            Action(
                rel="check",
                argv=["infralink", "check"],
                command="infralink check",
                description="Check declared edge health",
                safe=True,
            )
        ],
    ).model_dump(mode="json", exclude_none=True)

    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["result"]["valid"] is True


@pytest.mark.parametrize(
    ("ok", "result", "error"),
    [
        (True, None, None),
        (
            True,
            {"version": "0.2.0", "cli_schema_version": "infralink.cli/v1"},
            ErrorDetail(code="x", message="x"),
        ),
        (False, {"version": "0.2.0", "cli_schema_version": "infralink.cli/v1"}, None),
        (False, None, None),
        (True, None, ErrorDetail(code="x", message="x")),
    ],
)
def test_envelope_rejects_inconsistent_outcomes(
    ok: bool, result: object | None, error: ErrorDetail | None
) -> None:
    with pytest.raises(ValidationError, match="ok must select exactly one"):
        Envelope[VersionResult](
            ok=ok,
            command=context(),
            result=result,
            error=error,
            next_actions=[],
        )


def test_action_binding_and_diagnostic_literals_are_closed() -> None:
    action = Action(
        rel="show",
        argv=["infralink", "host", "show", "{host_id}"],
        command="infralink host show '{host_id}'",
        description="Show a host",
        safe=True,
        templated=True,
        bindings={"host_id": Binding(type="string", required=True, source="result.items[].id")},
    )
    assert action.bindings["host_id"].type == "string"
    with pytest.raises(ValidationError):
        Diagnostic(code="bad", message="Bad", severity="info")


@pytest.mark.parametrize(
    ("model", "field", "values", "maximum"),
    [
        (HostSummary, "services", host().model_dump(), 128),
        (HostSummary, "projects", host().model_dump(), 64),
        (EdgeSummary, "secret_refs", edge().model_dump(by_alias=True), 32),
        (ServiceSummary, "host_ids", service().model_dump(), 128),
        (ServiceSummary, "ports", service().model_dump(), 64),
        (ServiceSummary, "protocols", service().model_dump(), 32),
        (SecretReferenceStatus, "location_preview", secret().model_dump(), 16),
    ],
)
def test_summary_previews_have_published_maximums(
    model: type[ContractModel], field: str, values: dict[str, Any], maximum: int
) -> None:
    values[field] = [values[field][0]] * (maximum + 1)
    with pytest.raises(ValidationError):
        model.model_validate(values)


def test_all_command_result_contracts_have_typed_minimum_shapes() -> None:
    diagnostic = Diagnostic(code="ok", message="OK", severity="warning")
    artifact = Artifact(path="out.json", media_type="application/json", sha256="abc")
    check = CheckResult(
        edge_id="edge-1",
        healthy=True,
        status="healthy",
        latency_ms=1.5,
        error_code=None,
    )

    results = [
        RootResult(
            version="0.2.0",
            commands=[
                {"name": "validate", "description": "Validate", "usage": "infralink validate"}
            ],
        ),
        HelpResult(
            path=["resolve"],
            description="Resolve",
            arguments=[{"name": "edge_id", "type": "string", "required": True}],
            options=[{"name": "limit", "type": "integer", "required": False}],
            examples=["infralink resolve edge-1"],
        ),
        VersionResult(version="0.2.0", cli_schema_version="infralink.cli/v1"),
        InfoResult(
            sources={"registry": "registry.yml", "edges": "edges.yml"},
            summary={"host_count": 1, "service_count": 1, "edge_count": 1},
        ),
        HostListResult(items=[host()], page=PageInfo(limit=100, returned=1, total=1)),
        HostShowResult(host=host(), services=page(["api"]), projects=page(["core"])),
        ServiceListResult(items=[service()], page=PageInfo(limit=100, returned=1, total=1)),
        ServiceShowResult(
            service=service(),
            hosts=page(["host-1"]),
            ports=page([443]),
            protocols=page(["https"]),
        ),
        EdgeListResult(items=[edge()], page=PageInfo(limit=100, returned=1, total=1)),
        EdgeShowResult(edge=edge(), secret_refs=page(["secret://api"])),
        ValidateResult(
            valid=True,
            errors=page([]),
            warnings=page([diagnostic]),
            summary={"error_count": 0, "warning_count": 1},
        ),
        ResolveResult(
            edge=edge(),
            endpoint=Endpoint(host="host.example", port=443, protocol="https"),
            connection_template=None,
            secret_refs=page(["secret://api"]),
        ),
        CheckCommandResult(
            healthy=True,
            checks=page([check]),
            summary={"total": 1, "healthy": 1, "unhealthy": 0},
        ),
        AppListResult(
            items=[AppSummary(id="core", service_count=1, edge_count=1)],
            page=PageInfo(limit=100, returned=1, total=1),
        ),
        AppShowResult(
            app=AppSummary(id="core", service_count=1, edge_count=1),
            services=page([service()]),
            edges=page([edge()]),
        ),
        AnalyzeResult(
            analysis={
                "host_count": 1,
                "service_count": 1,
                "edge_count": 1,
                "diagnostics": page([]),
            },
            artifacts=page([artifact]),
        ),
        ArtifactResult(artifacts=page([artifact]), summary={"artifact_count": 1}),
        SecretsInspectResult(
            references=page([secret()]),
            locations=page([SourceLocation(source="edges", path="edges.edge-1.secret_ref")]),
            summary={"total": 1, "present": 0, "missing": 0, "accessible": 0, "denied": 0},
        ),
        SecretsAuditResult(
            provider="bws",
            references=page([secret()]),
            summary={"total": 1, "present": 1, "missing": 0, "accessible": 1, "denied": 0},
        ),
    ]

    assert len(results) == 19


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_generated_command_schema_is_canonical_draft_2020_12(name: str) -> None:
    schema_path = ROOT / "src/infralink/schemas/cli/v1" / f"{name}.json"
    raw = schema_path.read_text(encoding="utf-8")
    schema = json.loads(raw)

    Draft202012Validator.check_schema(schema)
    assert raw == json.dumps(schema, indent=2, sort_keys=True) + "\n"


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_generated_schema_publishes_and_enforces_outcome_invariant(name: str) -> None:
    schema = json.loads(
        (ROOT / "src/infralink/schemas/cli/v1" / f"{name}.json").read_text(encoding="utf-8")
    )
    assert schema["oneOf"] == [
        {
            "properties": {
                "ok": {"const": True},
                "result": {"not": {"type": "null"}},
            },
            "required": ["ok", "result"],
            "not": {"required": ["error"]},
        },
        {
            "properties": {
                "ok": {"const": False},
                "error": {"not": {"type": "null"}},
            },
            "required": ["ok", "error"],
            "not": {"required": ["result"]},
        },
    ]

    base = {
        "schema_version": "infralink.cli/v1",
        "command": context().model_dump(mode="json"),
        "next_actions": [],
    }
    invalid_documents = [
        {**base, "ok": True, "result": {}, "error": {"code": "x", "message": "x"}},
        {**base, "ok": False},
        {**base, "ok": False, "result": {}},
        {**base, "ok": True, "error": {"code": "x", "message": "x"}},
        {**base, "ok": True, "result": None},
        {**base, "ok": False, "error": None},
    ]
    validator = Draft202012Validator(schema)
    assert all(not validator.is_valid(document) for document in invalid_documents)
