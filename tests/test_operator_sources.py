from __future__ import annotations

from pathlib import Path

import pytest
from agent_surface import OperationError

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
