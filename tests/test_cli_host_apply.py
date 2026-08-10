from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from infralink.cli.main import cli
from tests.cli_helpers import assert_schema


HOSTS = (
    ("32a3324f-c3d0-4a4f-9587-52c099bcb3fb", "relaxgg-db-es1"),
    ("7ffe46b7-0eb4-40cb-8e14-ea679b9948f4", "cyberstorm-watchtower"),
    ("9157ddeb-cb6d-4d55-8252-9db358f5d932", "cyberstorm-citadel"),
)
HOST_ID, HOST_NAME = HOSTS[0]


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _registry_checkout(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "registry"
    (root / "release-channels").mkdir(parents=True)
    for host_id, canonical_name in HOSTS:
        host = root / "hosts" / host_id
        host.mkdir(parents=True)
        (host / "manifest.yml").write_text(
            "hosts:\n"
            f"  {host_id}:\n"
            f"    canonical_name: {canonical_name}\n"
            "    status: provisioning\n"
            "    tailscale_ip: 100.64.68.83\n",
            encoding="utf-8",
        )
        (host / "operations").mkdir()
        (host / "operations" / "release-policy.yml").write_text(
            "schema_version: infralink.release-admission.v1\n"
            f"host_uuid: {host_id}\n"
            "mode: release-channel\n"
            "registry:\n  remote: https://gitea.i.cyberstorm.dev/relaxgg/infra-registry.git\n"
            "release:\n  channel: v2\n"
            "  allowed_signers_file: /etc/infralink/release-admission/allowed_signers\n"
            "  recent_window: 20\n  maximum_candidates: 5\n",
            encoding="utf-8",
        )
    (root / "release-channels" / "v2.yml").write_text(
        "schema_version: infralink.release-target-set.v1\n"
        "channel: v2\n"
        "targets:\n"
        + "".join(
            f"  - host_uuid: {host_id}\n    policy: hosts/{host_id}/operations/release-policy.yml\n"
            for host_id, _ in HOSTS
        ),
        encoding="utf-8",
    )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "registry")
    return root / "hosts", _git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize(("host_id", "canonical_name"), HOSTS)
def test_host_apply_submits_each_target_through_one_opaque_operation_for_an_immutable_registry_revision(
    tmp_path: Path, monkeypatch, host_id: str, canonical_name: str
) -> None:
    from infralink.cli import operations

    registry, revision = _registry_checkout(tmp_path)
    submitted: dict[str, object] = {}

    class Provider:
        def submit(self, request: operations.ApplyRequest) -> operations.OperationRecord:
            submitted.update(request.as_payload())
            return operations.OperationRecord(id="op_01J00000000000000000000000", state="queued")

        def status(self, operation_id: str) -> operations.OperationRecord:
            raise AssertionError("default apply must not poll")

    monkeypatch.setattr(operations, "operation_provider_from_environment", lambda: Provider())

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "apply", host_id])

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "host-apply")
    assert submitted == {
        "host_uuid": host_id,
        "registry_revision": revision,
        "selector": "release-channels/v2.yml",
    }
    assert payload["result"] == {
        "operation": {"id": "op_01J00000000000000000000000", "state": "queued"},
        "target": {"type": "host", "id": host_id, "canonical_name": canonical_name},
    }
    assert payload["next_actions"] == [
        {
            "rel": "status",
            "command": "infralink operation status op_01J00000000000000000000000",
            "description": "Check host apply progress",
            "safe": True,
        }
    ]
    assert "release" not in response.output
    assert "publisher" not in response.output


def test_host_apply_wait_polls_the_submitted_operation_until_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    from infralink.cli import operations

    registry, _ = _registry_checkout(tmp_path)

    class Provider:
        def submit(self, request: operations.ApplyRequest) -> operations.OperationRecord:
            return operations.OperationRecord(id="op_01J00000000000000000000000", state="queued")

        def status(self, operation_id: str) -> operations.OperationRecord:
            return operations.OperationRecord(
                id=operation_id,
                state="converged",
                target={"type": "host", "id": HOST_ID, "canonical_name": "relaxgg-db-es1"},
            )

    monkeypatch.setattr(operations, "operation_provider_from_environment", lambda: Provider())
    monkeypatch.setattr("infralink.cli.operations.time.sleep", lambda _: None)

    response = CliRunner().invoke(
        cli,
        ["--registry", str(registry), "host", "apply", HOST_ID, "--wait", "--timeout", "1"],
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "host-apply")
    assert payload["result"]["operation"]["state"] == "converged"
    assert payload["result"]["operation"]["id"] == "op_01J00000000000000000000000"


@pytest.mark.parametrize(("host_id", "canonical_name"), HOSTS)
def test_operation_status_is_a_resumable_provider_poll(
    monkeypatch, host_id: str, canonical_name: str
) -> None:
    from infralink.cli import operations

    class Provider:
        def submit(self, request: operations.ApplyRequest) -> operations.OperationRecord:
            raise AssertionError("status must not submit")

        def status(self, operation_id: str) -> operations.OperationRecord:
            assert operation_id == "op_01J00000000000000000000000"
            return operations.OperationRecord(
                id=operation_id,
                state="applying",
                target={"type": "host", "id": host_id, "canonical_name": canonical_name},
            )

    monkeypatch.setattr(operations, "operation_provider_from_environment", lambda: Provider())
    response = CliRunner().invoke(cli, ["operation", "status", "op_01J00000000000000000000000"])

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "operation-status")
    assert payload["result"]["operation"] == {
        "id": "op_01J00000000000000000000000",
        "state": "applying",
    }
    assert payload["result"]["target"] == {
        "type": "host",
        "id": host_id,
        "canonical_name": canonical_name,
    }
