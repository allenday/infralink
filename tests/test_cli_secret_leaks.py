import json
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner, Result

from infralink.cli.main import cli
from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry
from infralink.health.checks import HealthCheckResult

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
EDGE_ID = "058e29ff-57b9-47c8-b6fa-0914ac03e25c"
TARGET_ID = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
SOURCE_ID = "fa2b9872-d94c-4b20-a73a-57a205560769"


def _write_canary_topology(tmp_path: Path, canary: str) -> tuple[Path, Path]:
    registry = yaml.safe_load((EXAMPLES / "registry.yml").read_text(encoding="utf-8"))
    registry["hosts"][TARGET_ID].setdefault("provider_metadata", {})[
        "password_value"
    ] = canary
    registry_path = tmp_path / "registry.yml"
    registry_path.write_text(yaml.safe_dump(registry), encoding="utf-8")

    edges = yaml.safe_load((EXAMPLES / "edges.yml").read_text(encoding="utf-8"))
    edges["edges"][0].setdefault("metadata", {})["credential_value"] = canary
    edges_path = tmp_path / "edges.yml"
    edges_path.write_text(yaml.safe_dump(edges), encoding="utf-8")

    applications = {
        "schema_version": "1.0",
        "applications": {
            "core": {
                "description": f"Loaded secret value: {canary}",
                "members": [
                    {"host": SOURCE_ID, "services": ["app-worker"]},
                    {"host": TARGET_ID, "services": ["postgresql"]},
                ],
                "edges": [EDGE_ID],
            }
        },
    }
    (tmp_path / "applications.yml").write_text(
        yaml.safe_dump(applications),
        encoding="utf-8",
    )
    return registry_path, edges_path


def _leaf_paths(group: click.Group, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = set()
    context = click.Context(group)
    for name in group.list_commands(context):
        command = group.get_command(context, name)
        assert command is not None
        path = (*prefix, name)
        if isinstance(command, click.Group):
            paths.update(_leaf_paths(command, path))
        else:
            paths.add(path)
    return paths


def _assert_no_canary(canary: str, label: str, result: Result) -> None:
    output = result.output
    stderr = result.stderr
    exception = result.exception
    assert stderr == "", label
    assert output.count("\n") == 1, label
    payload = json.loads(output)
    observables = (
        output,
        stderr,
        str(exception),
        repr(exception),
        json.dumps(payload, sort_keys=True),
    )
    assert all(canary not in observable for observable in observables), label


def test_every_live_cli_path_keeps_loaded_secret_values_out_of_observables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "repository-wide-loaded-secret-value-canary"
    registry_path, edges_path = _write_canary_topology(tmp_path, canary)

    loaded_registry = Registry.load(registry_path)
    loaded_edge = EdgeSet.load(edges_path).get(EDGE_ID)
    loaded_app = loaded_registry.applications.get_application("core")
    assert loaded_registry.get_by_uuid(TARGET_ID).provider_metadata["password_value"] == canary
    assert loaded_edge is not None
    assert loaded_edge._schema.metadata.model_extra["credential_value"] == canary
    assert loaded_app is not None
    assert loaded_app.description.endswith(canary)

    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: HealthCheckResult(
            edge_id=edge.id,
            edge_type=edge.type.value,
            target_endpoint="redacted",
            healthy=True,
            latency_ms=1.0,
            message=None,
            criticality=edge.criticality.value,
            check_type="tcp",
            timestamp=0.0,
        ),
    )

    source = [
        "--registry",
        str(registry_path),
        "--edges",
        str(edges_path),
    ]
    artifacts = tmp_path / "artifacts"
    invocations: dict[tuple[str, ...], list[str]] = {
        (): source,
        ("analyze",): [
            *source,
            "analyze",
            "--registry",
            str(registry_path),
            "--output",
            str(artifacts / "analyze"),
        ],
        ("app", "list"): [*source, "app", "list"],
        ("app", "show"): [*source, "app", "show", "core"],
        ("check",): [*source, "check", "--edge", EDGE_ID],
        ("diagram",): [
            *source,
            "diagram",
            "--format",
            "d2",
            "--output",
            str(artifacts / "diagrams"),
        ],
        ("docs",): [
            *source,
            "docs",
            "--output",
            str(artifacts / "docs"),
        ],
        ("edge", "show"): [*source, "edge", "show", EDGE_ID],
        ("edges-list",): [*source, "edges-list"],
        ("host", "show"): [*source, "host", "show", TARGET_ID],
        ("hosts",): [*source, "hosts"],
        ("info",): [*source, "info"],
        ("resolve",): [*source, "resolve", EDGE_ID],
        ("service", "show"): [*source, "service", "show", "postgresql"],
        ("services",): [*source, "services"],
        ("validate",): [*source, "validate", "--check-resolution"],
        ("version",): [*source, "version"],
    }
    discovered = _leaf_paths(cli)
    assert discovered == {*(set(invocations) - {()}), ("help",)}

    runner = CliRunner()
    for path, argv in invocations.items():
        result = runner.invoke(cli, argv)
        _assert_no_canary(canary, " ".join(path) or "root", result)

    help_paths = {
        (),
        *discovered,
        ("app",),
        ("edge",),
        ("host",),
        ("service",),
    }
    for path in sorted(help_paths):
        result = runner.invoke(cli, [*source, "help", *path])
        _assert_no_canary(canary, f"help {' '.join(path)}".rstrip(), result)

    for artifact in artifacts.rglob("*"):
        if artifact.is_file():
            assert canary not in artifact.read_text(encoding="utf-8"), artifact
