from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from agent_surface import OperationError
from click.testing import CliRunner
from pydantic import ValidationError

from infralink.agent_surface import operation_error_exit_code
from infralink.cli.errors import ExitCode
from infralink.operator_operations.topology import HostShowRequest, show_declared_host
from infralink.operator_sources import SourceRequest, load_info_sources, load_registry, load_sources
from infralink.operator_surface import operator_click_adapter, operator_surface


def test_load_sources_discovers_one_declared_edge_file(tmp_path: Path) -> None:
    hosts = tmp_path / "hosts" / "11111111-1111-4111-8111-111111111111"
    hosts.mkdir(parents=True)
    (hosts / "manifest.yml").write_text(
        "hosts:\n  11111111-1111-4111-8111-111111111111:\n"
        "    canonical_name: host-1\n    status: provisioning\n",
        encoding="utf-8",
    )
    edges = tmp_path / "topology/production/declared"
    edges.mkdir(parents=True)
    (edges / "edges.yml").write_text("edges: []\n", encoding="utf-8")

    loaded = load_sources(SourceRequest(registry=tmp_path))

    assert loaded.registry_path == tmp_path.resolve()
    assert loaded.edges_path == (edges / "edges.yml").resolve()
    assert loaded.registry.get("11111111-1111-4111-8111-111111111111") is not None
    assert len(loaded.edges) == 0


def test_load_registry_uses_the_configured_checkout_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = tmp_path / "registry" / "hosts" / "11111111-1111-4111-8111-111111111111"
    hosts.mkdir(parents=True)
    (hosts / "manifest.yml").write_text(
        "hosts:\n  11111111-1111-4111-8111-111111111111:\n"
        "    canonical_name: host-1\n    status: provisioning\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yml"
    config.write_text(f"registry: {tmp_path / 'registry'}\n", encoding="utf-8")
    monkeypatch.setenv("INFRALINK_CONFIG", str(config))

    loaded = load_registry(SourceRequest())

    assert loaded.registry_path == (tmp_path / "registry").resolve()


def test_load_registry_requires_config_or_explicit_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRALINK_CONFIG", str(tmp_path / "absent.yml"))

    with pytest.raises(OperationError) as error:
        load_registry(SourceRequest())

    assert error.value.code == "configuration_required"


def test_generated_click_preserves_configuration_error_exit_taxonomy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRALINK_CONFIG", str(tmp_path / "absent.yml"))

    result = CliRunner().invoke(
        operator_click_adapter().command(),
        ["host", "list", "--format", "json"],
    )

    assert result.exit_code == 3
    assert json.loads(result.output)["error"]["code"] == "configuration_required"


def test_generated_click_preserves_app_missing_source_usage_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRALINK_CONFIG", str(tmp_path / "absent.yml"))

    result = CliRunner().invoke(
        operator_click_adapter().command(),
        ["app", "list", "--format", "json"],
    )

    assert result.exit_code == 2
    assert json.loads(result.output)["error"]["code"] == "configuration_required"

    async def exercise_mcp() -> None:
        async with Client(operator_mcp_adapter().server) as client:
            response = await client.call_tool("app.list", {})
        assert response.is_error is True
        assert response.structured_content["error"]["code"] == "configuration_required"

    import asyncio

    from mcp import Client

    from infralink.operator_surface import operator_mcp_adapter

    asyncio.run(exercise_mcp())


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("usage_error", ExitCode.USAGE_ERROR),
        ("invalid_cursor", ExitCode.USAGE_ERROR),
        ("configuration_required", ExitCode.INPUT_ERROR),
        ("entity_not_found", ExitCode.INPUT_ERROR),
        ("provider_timeout", ExitCode.PROVIDER_ERROR),
        ("unsupported_platform", ExitCode.UNSUPPORTED_PLATFORM),
        ("internal_error", ExitCode.INTERNAL_ERROR),
        ("artifact_io_failed", ExitCode.ARTIFACT_IO_ERROR),
        ("unknown_future_code", ExitCode.INTERNAL_ERROR),
    ],
)
def test_typed_operation_exit_taxonomy_is_unambiguous(code: str, expected: ExitCode) -> None:
    assert operation_error_exit_code(code) == int(expected)


def test_load_sources_reports_a_missing_edge_declaration(tmp_path: Path) -> None:
    (tmp_path / "hosts").mkdir()

    with pytest.raises(OperationError, match="no edge declaration") as error:
        load_sources(SourceRequest(registry=tmp_path))

    assert error.value.code == "configuration_required"
    assert error.value.fix == (
        "Pass --edges with the declaration path or add exactly one edges.yml to the registry checkout."
    )
    assert load_registry(SourceRequest(registry=tmp_path)).registry_path == tmp_path.resolve()


def test_load_sources_rejects_ambiguous_discovered_edge_files(tmp_path: Path) -> None:
    (tmp_path / "hosts").mkdir()
    first = tmp_path / "topology/first/edges.yml"
    second = tmp_path / "topology/second/edges.yml"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("edges: []\n", encoding="utf-8")
    second.write_text("edges: []\n", encoding="utf-8")

    with pytest.raises(OperationError, match="ambiguous edge declarations") as error:
        load_sources(SourceRequest(registry=tmp_path))

    assert error.value.code == "configuration_required"
    assert error.value.fix == "Pass --edges with the intended edge declaration path."


def test_load_sources_keeps_an_explicit_edge_file_authoritative(tmp_path: Path) -> None:
    (tmp_path / "hosts").mkdir()
    for name in ("first", "second"):
        candidate = tmp_path / "topology" / name / "edges.yml"
        candidate.parent.mkdir(parents=True)
        candidate.write_text("edges: []\n", encoding="utf-8")
    explicit = tmp_path / "selected-edge-declaration.yml"
    explicit.write_text("edges: []\n", encoding="utf-8")

    loaded = load_sources(SourceRequest(registry=tmp_path, edges=explicit))

    assert loaded.edges_path == explicit.resolve()


def test_load_sources_fails_closed_when_companion_scan_exceeds_its_fixed_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "hosts").mkdir()
    (tmp_path / "empty-one").mkdir()
    (tmp_path / "empty-two").mkdir()
    monkeypatch.setattr("infralink.operator_sources.REGISTRY_COMPANION_SCAN_MAX_ENTRIES", 2)

    with pytest.raises(OperationError, match="fixed entry limit") as error:
        load_sources(SourceRequest(registry=tmp_path))

    assert error.value.code == "configuration_required"
    assert error.value.details == (
        {
            "source": "edges",
            "registry": str(tmp_path),
            "filename": "edges.yml",
            "reason": "scan_limit_exceeded",
        },
    )


def test_load_sources_translates_companion_scan_io_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "hosts").mkdir()
    monkeypatch.setattr(
        "infralink.operator_sources.os.scandir",
        lambda _path: (_ for _ in ()).throw(OSError("fixture scan failure")),
    )

    with pytest.raises(OperationError, match="could not be scanned") as error:
        load_sources(SourceRequest(registry=tmp_path))

    assert error.value.code == "configuration_required"
    assert error.value.details == (
        {
            "source": "edges",
            "registry": str(tmp_path),
            "filename": "edges.yml",
            "reason": "scan_failed",
        },
    )


@pytest.mark.parametrize("relative", ["hosts", "legacy-registry.yml"])
def test_load_registry_requires_the_checkout_root(tmp_path: Path, relative: str) -> None:
    (tmp_path / "hosts").mkdir()
    target = tmp_path / relative
    if target.suffix:
        target.write_text("hosts: {}\n", encoding="utf-8")

    with pytest.raises(OperationError, match="checkout root"):
        load_registry(SourceRequest(registry=target))


def test_info_uses_the_same_checkout_root_contract_and_companion_actions(tmp_path: Path) -> None:
    hosts = tmp_path / "hosts" / "11111111-1111-4111-8111-111111111111"
    hosts.mkdir(parents=True)
    (hosts / "manifest.yml").write_text(
        "hosts:\n  11111111-1111-4111-8111-111111111111:\n"
        "    canonical_name: host-1\n    status: active\n",
        encoding="utf-8",
    )
    edges = tmp_path / "topology" / "production" / "edges.yml"
    edges.parent.mkdir(parents=True)
    edges.write_text("edges: []\n", encoding="utf-8")

    loaded = load_info_sources(SourceRequest(registry=tmp_path))
    result = CliRunner().invoke(
        operator_click_adapter().command(),
        ["info", "--registry", str(tmp_path), "--format", "json"],
    )

    assert loaded.registry_path == tmp_path.resolve()
    assert loaded.edges_path == edges.resolve()
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["result"]["sources"] == {
        "registry": str(tmp_path.resolve()),
        "edges": str(edges.resolve()),
    }
    assert all(str(tmp_path.resolve()) in action["command"] for action in payload["next_actions"])


def test_info_rejects_the_removed_standalone_yaml_source(tmp_path: Path) -> None:
    standalone = tmp_path / "registry.yml"
    standalone.write_text("hosts: {}\n", encoding="utf-8")

    with pytest.raises(OperationError, match="checkout root"):
        load_info_sources(SourceRequest(registry=standalone))

    result = CliRunner().invoke(
        operator_click_adapter().command(),
        ["info", "--registry", str(standalone), "--format", "json"],
    )
    assert result.exit_code == 3
    assert json.loads(result.output)["error"]["code"] == "input_load_failed"

    async def exercise_mcp() -> None:
        from mcp import Client

        from infralink.operator_surface import operator_mcp_adapter

        async with Client(operator_mcp_adapter().server) as client:
            response = await client.call_tool("info", {"registry": str(standalone)})
        assert response.is_error is True
        assert response.structured_content["error"]["code"] == "input_load_failed"

    import asyncio

    asyncio.run(exercise_mcp())


def test_read_only_operations_share_one_typed_source_boundary(tmp_path: Path) -> None:
    hosts = tmp_path / "hosts" / "11111111-1111-4111-8111-111111111111"
    hosts.mkdir(parents=True)
    (hosts / "manifest.yml").write_text(
        "hosts:\n  11111111-1111-4111-8111-111111111111:\n"
        "    canonical_name: host-1\n    status: provisioning\n",
        encoding="utf-8",
    )
    edges = tmp_path / "network/main-dev/edges"
    edges.mkdir(parents=True)
    (edges / "edges.yml").write_text("edges: []\n", encoding="utf-8")

    import asyncio

    host_result = asyncio.run(operator_surface.invoke("host.list", {"registry": tmp_path}))
    edge_result = asyncio.run(operator_surface.invoke("edge.list", {"registry": tmp_path}))
    info_result = asyncio.run(operator_surface.invoke("info", {"registry": tmp_path}))

    assert host_result.items == ["11111111-1111-4111-8111-111111111111"]
    assert edge_result.items == []
    assert info_result.summary.host_count == 1


def test_generated_click_projects_host_list_from_the_shared_operation(tmp_path: Path) -> None:
    hosts = tmp_path / "hosts" / "11111111-1111-4111-8111-111111111111"
    hosts.mkdir(parents=True)
    (hosts / "manifest.yml").write_text(
        "hosts:\n  11111111-1111-4111-8111-111111111111:\n"
        "    canonical_name: host-1\n    status: provisioning\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        operator_click_adapter().command(),
        ["host", "list", "--registry", str(tmp_path), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"]["parsed"]["path"] == ["host", "list"]
    assert payload["result"]["items"] == ["11111111-1111-4111-8111-111111111111"]


def test_topology_reads_are_registered_once_and_preserve_bounded_host_continuation(
    tmp_path: Path,
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    manifest = tmp_path / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n"
        "    canonical_name: host-1\n"
        "    status: active\n"
        "    projects: [one, two]\n"
        "    services:\n"
        "      alpha: {}\n"
        "      beta: {}\n",
        encoding="utf-8",
    )
    registered = {item.name for item in operator_surface.operations.list()}
    assert {
        "host.show",
        "service.show",
        "edge.show",
    } <= registered
    assert {"app.list", "app.show"} <= registered

    first = show_declared_host(HostShowRequest(registry=tmp_path, host_id=host_id, limit=1))
    assert first.services.items == ["alpha"]
    assert first.services.page.next_cursor is not None
    second = show_declared_host(
        HostShowRequest(
            registry=tmp_path,
            host_id=host_id,
            limit=1,
            cursor=first.services.page.next_cursor,
            collection="services",
        )
    )
    assert second.services.items == ["beta"]


def test_topology_host_cursor_remains_compatible_with_the_legacy_cli(
    tmp_path: Path,
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    manifest = tmp_path / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n"
        "    canonical_name: host-1\n"
        "    services: {alpha: {}, beta: {}}\n",
        encoding="utf-8",
    )
    legacy = CliRunner().invoke(
        operator_click_adapter().command(),
        ["host", "show", host_id, "--registry", str(tmp_path), "--limit", "1"],
    )
    assert legacy.exit_code == 0, legacy.output
    cursor = yaml.safe_load(legacy.output)["result"]["services"]["page"]["next_cursor"]

    typed = show_declared_host(
        HostShowRequest(
            registry=tmp_path,
            host_id=host_id,
            limit=1,
            cursor=cursor,
            collection="services",
        )
    )

    assert typed.services.items == ["beta"]


def test_topology_host_cursor_preserves_relative_registry_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n"
        "    canonical_name: host-1\n"
        "    services: {alpha: {}, beta: {}}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    legacy = CliRunner().invoke(
        operator_click_adapter().command(),
        ["host", "show", host_id, "--registry", "registry", "--limit", "1"],
    )
    assert legacy.exit_code == 0, legacy.output
    cursor = yaml.safe_load(legacy.output)["result"]["services"]["page"]["next_cursor"]

    typed = show_declared_host(
        HostShowRequest(
            registry=Path("registry"),
            host_id=host_id,
            limit=1,
            cursor=cursor,
            collection="services",
        )
    )

    assert typed.services.items == ["beta"]


def test_topology_host_cursor_preserves_relative_explicit_edges_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n"
        "    canonical_name: host-1\n"
        "    services: {alpha: {}, beta: {}}\n",
        encoding="utf-8",
    )
    (tmp_path / "edges.yml").write_text("edges: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    legacy = CliRunner().invoke(
        operator_click_adapter().command(),
        [
            "host",
            "show",
            host_id,
            "--registry",
            "registry",
            "--edges",
            "edges.yml",
            "--limit",
            "1",
        ],
    )
    assert legacy.exit_code == 0, legacy.output
    cursor = yaml.safe_load(legacy.output)["result"]["services"]["page"]["next_cursor"]

    typed = show_declared_host(
        HostShowRequest(
            registry=Path("registry"),
            edges=Path("edges.yml"),
            host_id=host_id,
            limit=1,
            cursor=cursor,
            collection="services",
        )
    )

    assert typed.services.items == ["beta"]


def test_topology_host_cursor_is_bound_to_its_checkout_path(tmp_path: Path) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    for name in ("first", "second"):
        manifest = tmp_path / name / "hosts" / host_id / "manifest.yml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            f"hosts:\n  {host_id}:\n"
            "    canonical_name: host-1\n"
            "    services: {alpha: {}, beta: {}}\n",
            encoding="utf-8",
        )
    first = show_declared_host(
        HostShowRequest(registry=tmp_path / "first", host_id=host_id, limit=1)
    )
    with pytest.raises(OperationError, match="Cursor is invalid"):
        show_declared_host(
            HostShowRequest(
                registry=tmp_path / "second",
                host_id=host_id,
                limit=1,
                cursor=first.services.page.next_cursor,
                collection="services",
            )
        )


def test_topology_paging_rejects_boolean_limits(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        HostShowRequest(registry=tmp_path, host_id="host-1", limit=True)
