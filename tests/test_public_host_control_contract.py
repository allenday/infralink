"""Behavioral transport parity for retained host control operations.

These are not compatibility tests for removed Click aliases.  They exercise
the public generated Click tree and native MCP projection against the same
typed handlers, including the control flags operators rely on in incidents.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from click.testing import CliRunner
from mcp import Client

from infralink.cli.main import cli
from infralink.cli.operations import ApplyRequest, OperationRecord
from infralink.mcp_server import create_server

HOST_ID = "32a3324f-c3d0-4a4f-9587-52c099bcb3fb"
HOST_NAME = "relaxgg-db-es1"


def _registry_checkout(tmp_path: Path) -> Path:
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    status: active\n"
        "    tailscale_ip: 100.64.68.83\n",
        encoding="utf-8",
    )
    for args in (
        ("init", "--quiet"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Test"),
        ("add", "."),
        ("commit", "--quiet", "-m", "registry"),
    ):
        subprocess.run(["git", "-C", str(registry), *args], check=True)
    return registry


def _apply_request() -> ApplyRequest:
    return ApplyRequest(
        host_uuid=HOST_ID,
        canonical_name=HOST_NAME,
        address="100.64.68.83",
        port=22,
        user="root",
        host_key_fingerprint="SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        unit="infralink-host-reconcile.service",
    )


async def _mcp_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with Client(create_server()) as client:
        response = await client.call_tool(name, arguments)
    assert response.is_error is False
    assert isinstance(response.structured_content, dict)
    return response.structured_content


def test_host_apply_dry_run_has_the_same_typed_plan_through_click_and_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.resolve_apply_request", lambda *_args: _apply_request()
    )
    monkeypatch.setattr(
        "infralink.cli.operations.validate_target_ssh_identity", lambda _request: None
    )

    click_result = CliRunner().invoke(
        cli,
        ["host", "apply", HOST_ID, "--registry", str(registry), "--dry-run", "--format", "json"],
    )
    assert click_result.exit_code == 0, click_result.output
    click_document = json.loads(click_result.output)
    mcp_document = asyncio.run(
        _mcp_call("host.apply", {"host_ref": HOST_ID, "registry": str(registry), "dry_run": True})
    )

    for document in (click_document, mcp_document):
        assert document["ok"] is True
        assert document["command"]["parsed"]["path"] == ["host", "apply"]
        assert document["result"]["dry_run"] is True
        assert document["result"]["target"] == {
            "type": "host",
            "id": HOST_ID,
            "canonical_name": HOST_NAME,
        }
        assert document["result"]["plan"]["dispatch_provider"] == "ssh"
        assert document["result"]["plan"]["reconcile_mode"] == "timer"
        assert document["result"]["plan"]["action_categories"] == [
            "registry_checkout",
            "render",
            "reconcile",
        ]


def test_host_apply_wait_dispatches_once_and_honors_the_typed_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    registry = _registry_checkout(tmp_path)
    request = _apply_request()
    record = OperationRecord(id=f"ssh/{HOST_ID}/8d6c4ad60e4a4b589fe35ad9e1760d56", state="queued")
    calls: list[object] = []

    class Provider:
        def submit(self, received: ApplyRequest) -> OperationRecord:
            calls.append(("submit", received))
            return record

        def status(self, _operation_id: str, _received: ApplyRequest) -> OperationRecord:
            raise AssertionError("wait_for_terminal is patched for this dispatch contract")

    def wait_for_terminal(
        provider: Provider, operation_id: str, received: ApplyRequest, *, timeout_seconds: int
    ) -> OperationRecord:
        calls.append(("wait", provider, operation_id, received, timeout_seconds))
        return OperationRecord(id=operation_id, state="converged")

    monkeypatch.setattr("infralink.cli.operations.resolve_apply_request", lambda *_args: request)
    monkeypatch.setattr("infralink.cli.operations.operation_provider", lambda: Provider())
    monkeypatch.setattr("infralink.cli.operations.wait_for_terminal", wait_for_terminal)

    document = asyncio.run(
        _mcp_call(
            "host.apply",
            {"host_ref": HOST_ID, "registry": str(registry), "wait": True, "timeout": 17},
        )
    )

    assert document["ok"] is True
    assert document["result"]["operation"] == {"id": record.id, "state": "converged"}
    assert document["result"]["dispatch"] == {"provider": "ssh", "status": "accepted"}
    assert calls[0] == ("submit", request)
    assert calls[1][0] == "wait"
    assert calls[1][2:] == (record.id, request, 17)


def test_host_logs_selects_bounded_public_or_diagnostic_evidence_on_both_transports(
    tmp_path: Path, monkeypatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.resolve_apply_request", lambda *_args: _apply_request()
    )
    monkeypatch.setattr(
        "infralink.cli.operations.inspect_target_logs", lambda _request: ["public line"]
    )
    monkeypatch.setattr(
        "infralink.cli.operations.inspect_target_diagnostic", lambda _request: ["diagnostic line"]
    )

    click_result = CliRunner().invoke(
        cli,
        ["host", "logs", HOST_ID, "--last-run", "--registry", str(registry), "--format", "json"],
    )
    assert click_result.exit_code == 0, click_result.output
    click_document = json.loads(click_result.output)
    diagnostic_document = asyncio.run(
        _mcp_call(
            "host.logs",
            {
                "host_ref": HOST_ID,
                "last_run": True,
                "diagnostic": True,
                "registry": str(registry),
            },
        )
    )

    assert click_document["result"]["lines"] == ["public line"]
    assert diagnostic_document["result"]["lines"] == ["diagnostic line"]
    for document in (click_document, diagnostic_document):
        assert document["command"]["parsed"]["path"] == ["host", "logs"]
        assert document["result"]["target"]["id"] == HOST_ID
