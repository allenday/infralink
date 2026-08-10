from __future__ import annotations

import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from infralink.cli.main import cli
from tests.cli_helpers import assert_schema

HOST_ID = "32a3324f-c3d0-4a4f-9587-52c099bcb3fb"
HOST_NAME = "relaxgg-db-es1"
UNIT = "self-deploy-v2-reconcile.service"
INVOCATION = "8d6c4ad60e4a4b589fe35ad9e1760d56"
FINGERPRINT = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _registry_checkout(tmp_path: Path, *, declared: bool = True) -> Path:
    root = tmp_path / "registry"
    host = root / "hosts" / HOST_ID
    host.mkdir(parents=True)
    (host / "manifest.yml").write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    status: active\n"
        "    tailscale_ip: 100.64.68.83\n",
        encoding="utf-8",
    )
    if declared:
        operations = host / "operations"
        operations.mkdir()
        (operations / "contract.yml").write_text(
            "schema_version: host-operations-contract.v1\n"
            "machine:\n"
            f"  uuid: {HOST_ID}\n"
            f"  canonical_name: {HOST_NAME}\n"
            "transport:\n"
            "  kind: ssh\n"
            "  host: 100.64.68.83\n"
            "  port: 22\n"
            "  user: root\n"
            f"  host_key_fingerprint: {FINGERPRINT}\n"
            "reconcile:\n"
            f"  unit: {UNIT}\n",
            encoding="utf-8",
        )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "registry")
    return root / "hosts"


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_host_apply_starts_only_declared_reconcile_unit_and_returns_opaque_run_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    calls: list[list[str]] = []
    fingerprints: list[str] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _completed(
            f"InvocationID={INVOCATION}\nActiveState=activating\nResult=success\nExecMainStatus=0\n"
        )

    monkeypatch.setattr("infralink.cli.operations.subprocess.run", fake_run)
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: (
            fingerprints.append(request.host_key_fingerprint),
            nullcontext(Path("/tmp/known-hosts")),
        )[1],
    )
    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "apply", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "host-apply")
    assert calls == [
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=/tmp/known-hosts",
            "-p",
            "22",
            "root@100.64.68.83",
            "sh",
            "-s",
            "--",
            UNIT,
        ]
    ]
    assert fingerprints == [FINGERPRINT]
    assert payload["result"] == {
        "operation": {
            "id": f"ssh/{HOST_ID}/{INVOCATION}",
            "state": "applying",
        },
        "target": {"type": "host", "id": HOST_ID, "canonical_name": HOST_NAME},
    }
    assert payload["next_actions"][0]["rel"] == "status"


def test_host_apply_wait_polls_exact_run_until_converged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    responses = iter(
        [
            _completed(
                f"InvocationID={INVOCATION}\n"
                "ActiveState=activating\nResult=success\nExecMainStatus=0\n"
            ),
            _completed(
                f"InvocationID={INVOCATION}\n"
                "ActiveState=inactive\nResult=success\nExecMainStatus=0\n"
            ),
        ]
    )
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run", lambda *args, **kwargs: next(responses)
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    monkeypatch.setattr("infralink.cli.operations.time.sleep", lambda _: None)

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "host", "apply", HOST_ID, "--wait", "--timeout", "1"]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "host-apply")
    assert payload["result"]["operation"]["state"] == "converged"
    assert payload["next_actions"][0]["rel"] == "doctor"


def test_host_apply_refuses_a_host_without_declared_ssh_reconcile_contract(tmp_path: Path) -> None:
    registry = _registry_checkout(tmp_path, declared=False)

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "apply", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "input_load_failed"
    assert "declared" in payload["error"]["message"].lower()


def test_host_apply_refuses_a_noncanonical_ssh_fingerprint(tmp_path: Path) -> None:
    registry = _registry_checkout(tmp_path)
    contract = registry / HOST_ID / "operations" / "contract.yml"
    contract.write_text(
        contract.read_text(encoding="utf-8").replace(FINGERPRINT, "SHA256:short"), encoding="utf-8"
    )
    _git(registry.parent, "add", ".")
    _git(registry.parent, "commit", "--quiet", "-m", "invalid fingerprint")

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "apply", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert "fingerprint" in payload["error"]["message"].lower()


def test_operation_status_queries_the_declared_host_local_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            f"InvocationID={INVOCATION}\nActiveState=inactive\nResult=success\nExecMainStatus=0\n"
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    operation_id = f"ssh/{HOST_ID}/{INVOCATION}"

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "operation", "status", operation_id]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "operation-status")
    assert payload["result"]["operation"] == {"id": operation_id, "state": "converged"}
    assert payload["result"]["target"] == {
        "type": "host",
        "id": HOST_ID,
        "canonical_name": HOST_NAME,
    }


def test_operation_status_reads_a_terminal_result_from_the_host_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            "InvocationID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            "ActiveState=inactive\nResult=success\nExecMainStatus=0\n"
            '{"ok":true,"run_id":"durable-host-local-result"}\n'
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    operation_id = f"ssh/{HOST_ID}/{INVOCATION}"

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "operation", "status", operation_id]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert payload["result"]["operation"] == {"id": operation_id, "state": "converged"}


def test_operation_status_refuses_a_run_reference_for_an_undeclared_host(tmp_path: Path) -> None:
    registry = _registry_checkout(tmp_path, declared=False)

    response = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(registry),
            "operation",
            "status",
            f"ssh/{HOST_ID}/{INVOCATION}",
        ],
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "input_load_failed"


def test_operation_status_explicitly_rejects_a_legacy_control_plane_reference() -> None:
    response = CliRunner().invoke(cli, ["operation", "status", "op_01J00000000000000000000000"])

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "provider_unavailable"
    assert "legacy" in payload["error"]["message"].lower()
