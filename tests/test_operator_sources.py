from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from agent_surface import OperationError
from agent_surface.adapters.click import ClickAdapter
from click.testing import CliRunner
from pydantic import ValidationError

from infralink.cli.main import cli
from infralink.operator_operations.topology import HostShowRequest, show_declared_host
from infralink.operator_sources import SourceRequest, load_registry, load_sources
from infralink.operator_surface import operator_surface


def test_load_sources_resolves_registry_root_and_default_edge_companion(tmp_path: Path) -> None:
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


def test_load_sources_reports_a_missing_edge_declaration(tmp_path: Path) -> None:
    (tmp_path / "hosts").mkdir()

    with pytest.raises(OperationError, match="Edge declaration source does not exist"):
        load_sources(SourceRequest(registry=tmp_path))

    assert load_registry(SourceRequest(registry=tmp_path)).registry_path == tmp_path.resolve()


@pytest.mark.parametrize("relative", ["hosts", "legacy-registry.yml"])
def test_load_registry_requires_the_checkout_root(tmp_path: Path, relative: str) -> None:
    (tmp_path / "hosts").mkdir()
    target = tmp_path / relative
    if target.suffix:
        target.write_text("hosts: {}\n", encoding="utf-8")

    with pytest.raises(OperationError, match="checkout root"):
        load_registry(SourceRequest(registry=target))


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
        ClickAdapter(operator_surface).command(),
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
        "app.list",
        "app.show",
    } <= registered

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
        cli,
        ["--registry", str(tmp_path), "host", "show", host_id, "--limit", "1"],
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
        cli,
        ["--registry", "registry", "host", "show", host_id, "--limit", "1"],
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
        cli,
        [
            "--registry",
            "registry",
            "--edges",
            "edges.yml",
            "host",
            "show",
            host_id,
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
