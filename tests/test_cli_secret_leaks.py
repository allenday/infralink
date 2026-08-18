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
from infralink.host_readiness import HostReadinessProbe
from infralink.secrets import SecretAudit

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
EDGE_ID = "058e29ff-57b9-47c8-b6fa-0914ac03e25c"
TARGET_ID = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
SOURCE_ID = "fa2b9872-d94c-4b20-a73a-57a205560769"


def _write_canary_topology(tmp_path: Path, canary: str) -> tuple[Path, Path, Path]:
    registry = yaml.safe_load((EXAMPLES / "registry.yml").read_text(encoding="utf-8"))
    for host in registry["hosts"].values():
        host["status"] = "terminated"
        host["bws_project"] = "00000000-0000-4000-8000-000000000001"
    registry["hosts"][TARGET_ID].setdefault("provider_metadata", {})["password_value"] = canary
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
    authoring_registry = tmp_path / "authoring-hosts"
    manifest = authoring_registry / TARGET_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        yaml.safe_dump({"hosts": {TARGET_ID: registry["hosts"][TARGET_ID]}}, sort_keys=False),
        encoding="utf-8",
    )
    return registry_path, edges_path, authoring_registry


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


def _assert_no_canary(
    canary: str,
    label: str,
    result: Result,
    *,
    expected_exit: int,
    expected_ok: bool,
) -> None:
    output = result.output
    stderr = result.stderr
    exception = result.exception
    assert result.exit_code == expected_exit, (label, output, repr(exception))
    assert stderr == "", label
    payload = yaml.safe_load(output)
    assert payload["ok"] is expected_ok, label
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
    registry_path, edges_path, authoring_registry = _write_canary_topology(tmp_path, canary)

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
    monkeypatch.setattr(
        "infralink.cli.main.SshReadinessTransport.probe",
        lambda self, address: HostReadinessProbe(
            reachable=False,
            hostname=None,
            machine_id=None,
            commands={},
            devops_account=False,
            devops_authorized_access=False,
            bws_config=False,
            self_deploy_runtime=False,
            self_deploy_timer_enabled=False,
            self_deploy_timer_active=False,
            error="ssh_unreachable",
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.secrets._build_bws_resolver",
        lambda: type(
            "OfflineAuditResolver",
            (),
            {
                "audit": staticmethod(
                    lambda references: [
                        SecretAudit(item.ref, item.project, True, True) for item in references
                    ]
                )
            },
        )(),
    )

    source = [
        "--registry",
        str(registry_path),
        "--edges",
        str(edges_path),
    ]
    monkeypatch.chdir(tmp_path)
    artifacts = Path("artifacts")
    release_validation = tmp_path / "release-validation.json"
    release_validation.write_text(
        json.dumps(
            {
                "schema_version": "infralink.release-validation.v1",
                "release_identity": "releases/core-v2/42",
                "registry_commit": "a" * 40,
                "controller_commit": "b" * 40,
                "annotated": True,
                "status": "active",
            }
        ),
        encoding="utf-8",
    )
    release_admission = tmp_path / "release-admission.yml"
    release_admission.write_text(
        """schema_version: infralink.release-admission.v1
selection:
  mode: release-channel
  channel: core-v2
  recent_window: 20
  maximum_candidates: 5
""",
        encoding="utf-8",
    )
    release_candidate = tmp_path / "release-candidate.json"
    release_candidate.write_text(
        json.dumps(
            {
                "schema_version": "infralink.release-candidate.v1",
                "release": {
                    "identity": "releases/core-v2/42",
                    "channel": "core-v2",
                    "sequence": 42,
                },
                "registry_commit": "a" * 40,
                "controller_commit": "b" * 40,
                "ci_receipt": {
                    "provider": "woodpecker",
                    "repository": "relaxgg/infra-registry",
                    "run": "576",
                },
                "artifacts": [{"path": "release/runtime.tar.gz", "sha256": "c" * 64}],
                "consumers": ["citadel"],
            }
        ),
        encoding="utf-8",
    )
    release_attestation = tmp_path / "release-attestation.json"
    release_attestation.write_text(
        json.dumps(
            {
                "schema_version": "infralink.release-attestation.v1",
                "release": {
                    "identity": "releases/core-v2/42",
                    "channel": "core-v2",
                    "sequence": 42,
                },
                "registry_commit": "a" * 40,
                "controller_commit": "b" * 40,
                "ci_receipt": {
                    "provider": "woodpecker",
                    "repository": "relaxgg/infra-registry",
                    "run": "576",
                },
                "artifacts": [{"path": "release/runtime.tar.gz", "sha256": "c" * 64}],
                "publisher_receipt": {
                    "provider": "woodpecker",
                    "repository": "relaxgg/infra-registry",
                    "run": "600",
                },
                "tag": {"name": "releases/core-v2/42", "object_sha1": "d" * 40},
                "consumers": ["citadel"],
            }
        ),
        encoding="utf-8",
    )
    invocations: dict[tuple[str, ...], tuple[list[str], int, bool]] = {
        (): (source, 0, True),
        ("analyze",): (
            [
                *source,
                "analyze",
                "--registry",
                str(registry_path),
                "--output",
                str(artifacts / "analyze"),
                "--no-edges",
                "--no-diagram",
            ],
            0,
            True,
        ),
        ("app", "list"): ([*source, "app", "list"], 0, True),
        ("app", "show"): ([*source, "app", "show", "core"], 0, True),
        ("check",): ([*source, "check", "--edge", EDGE_ID], 0, True),
        ("doctor",): ([*source, "doctor", "host", TARGET_ID], 2, False),
        ("diagram",): (
            [
                *source,
                "diagram",
                "--format",
                "d2",
                "--include-terminated",
                "--output",
                str(artifacts / "diagrams"),
            ],
            0,
            True,
        ),
        ("docs",): (
            [
                *source,
                "docs",
                "--output",
                str(artifacts / "docs"),
            ],
            0,
            True,
        ),
        ("edge", "show"): ([*source, "edge", "show", EDGE_ID], 0, True),
        ("edge", "list"): ([*source, "edge", "list"], 0, True),
        ("host", "show"): ([*source, "host", "show", TARGET_ID], 0, True),
        ("host", "list"): ([*source, "host", "list"], 0, True),
        ("host", "create"): (
            ["host", "create", "--name", "secret-leak-test", "--address", "192.0.2.1"],
            0,
            True,
        ),
        # The fixture deliberately has a public documentation address; bootstrap
        # now rejects it before any provider or secret interaction.
        ("host", "bootstrap"): ([*source, "host", "bootstrap", TARGET_ID, "--plan"], 2, False),
        ("host", "apply"): ([*source, "host", "apply", TARGET_ID, "--dry-run"], 3, False),
        ("host", "status"): ([*source, "host", "status", TARGET_ID], 3, False),
        ("host", "logs"): ([*source, "host", "logs", TARGET_ID, "--last-run"], 3, False),
        ("host", "verifier"): ([*source, "host", "verifier", TARGET_ID], 3, False),
        ("info",): ([*source, "info"], 0, True),
        ("operation", "status"): (
            [
                *source,
                "operation",
                "status",
                "ssh/32a3324f-c3d0-4a4f-9587-52c099bcb3fb/8d6c4ad6-0e4a-4b58-9fe3-5ad9e1760d56",
            ],
            3,
            False,
        ),
        ("resolve",): ([*source, "resolve", EDGE_ID], 0, True),
        ("secrets", "audit"): ([*source, "secrets", "audit"], 0, True),
        ("secrets", "inspect"): ([*source, "secrets", "inspect"], 0, True),
        ("service", "show"): ([*source, "service", "show", "postgresql"], 0, True),
        ("service", "list"): ([*source, "service", "list"], 0, True),
        ("validate",): ([*source, "validate", "--check-resolution"], 0, True),
        ("version",): ([*source, "version"], 0, True),
        ("capabilities",): (["--output", "json", "capabilities"], 0, True),
        ("explain",): (["--output", "json", "explain", "schema-version-unsupported"], 0, True),
        ("project", "observation"): (
            [
                "--output",
                "json",
                "project",
                "observation",
                "--source",
                str(EXAMPLES / "observation"),
                "--as-of",
                "2026-08-04T00:00:00Z",
            ],
            0,
            True,
        ),
        ("project", "secrets"): (
            [
                "--output",
                "json",
                "project",
                "secrets",
                "--source",
                str(EXAMPLES / "observation"),
                "--as-of",
                "2026-08-04T00:00:00Z",
            ],
            0,
            True,
        ),
        ("project", "view"): (
            [
                "--output",
                "json",
                "project",
                "view",
                "service-overview",
                "--source",
                str(EXAMPLES / "observation"),
                "--as-of",
                "2026-08-04T00:00:00Z",
            ],
            0,
            True,
        ),
        ("project", "readiness"): (
            [
                "--output",
                "json",
                "project",
                "readiness",
                "ci-release",
                "--source",
                str(EXAMPLES / "observation"),
                "--as-of",
                "2026-08-04T00:00:00Z",
            ],
            0,
            True,
        ),
        ("release", "inspect"): (
            [
                "release",
                "inspect",
                "--release-validation",
                str(release_validation),
                "--admission",
                str(release_admission),
            ],
            0,
            True,
        ),
        ("release", "validate-candidate"): (
            ["release", "validate-candidate", "--candidate", str(release_candidate)],
            0,
            True,
        ),
        ("release", "render-publisher-request"): (
            [
                "release",
                "render-publisher-request",
                "--candidate",
                str(release_candidate),
                "--admission",
                str(release_admission),
            ],
            1,
            False,
        ),
        ("release", "inspect-attestation"): (
            ["release", "inspect-attestation", "--attestation", str(release_attestation)],
            0,
            True,
        ),
        ("registry", "host", "get"): (
            ["--registry", str(authoring_registry), "registry", "host", "get", TARGET_ID],
            0,
            True,
        ),
        ("registry", "host", "patch"): (
            [
                "--registry",
                str(authoring_registry),
                "registry",
                "host",
                "patch",
                TARGET_ID,
                "--set",
                "provider_metadata.password_value=rotated",
            ],
            0,
            True,
        ),
    }
    discovered = _leaf_paths(cli)
    # `mcp serve` owns a long-running JSON-RPC transport and cannot be invoked
    # as a one-shot CLI envelope. Its delegated command boundary is covered by
    # tests/test_mcp_server.py.
    assert discovered == {*(set(invocations) - {()}), ("help",), ("mcp", "serve")}

    runner = CliRunner()
    for path, (argv, expected_exit, expected_ok) in invocations.items():
        result = runner.invoke(cli, argv)
        _assert_no_canary(
            canary,
            " ".join(path) or "root",
            result,
            expected_exit=expected_exit,
            expected_ok=expected_ok,
        )

    help_paths = {
        (),
        *discovered,
        ("app",),
        ("edge",),
        ("host",),
        ("registry",),
        ("registry", "host"),
        ("secrets",),
        ("service",),
    }
    for path in sorted(help_paths):
        result = runner.invoke(cli, [*source, "help", *path])
        _assert_no_canary(
            canary,
            f"help {' '.join(path)}".rstrip(),
            result,
            expected_exit=0,
            expected_ok=True,
        )

    for artifact in artifacts.rglob("*"):
        if artifact.is_file():
            assert canary not in artifact.read_text(encoding="utf-8"), artifact
