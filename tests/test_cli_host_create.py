from __future__ import annotations

from pathlib import Path
from uuid import UUID

import yaml
from click.testing import CliRunner

from infralink.cli.main import cli
from infralink.core.registry import Registry


def _registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "hosts"
    root.mkdir()
    return root


def test_host_create_dry_run_outputs_a_schema_valid_scaffold_without_writing(
    tmp_path: Path,
) -> None:
    registry_root = _registry_root(tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(registry_root),
            "host",
            "create",
            "--name",
            "new-node",
            "--address",
            "100.64.1.9",
        ],
    )

    payload = yaml.safe_load(result.output)
    assert result.exit_code == 0
    assert payload["result"]["mode"] == "dry_run"
    host_id = payload["result"]["host_id"]
    assert UUID(host_id).version == 4
    assert payload["result"]["manifest"] == {
        "hosts": {
            host_id: {
                "canonical_name": "new-node",
                "status": "provisioning",
                "tailscale_ip": "100.64.1.9",
            }
        }
    }
    assert payload["result"]["address"] == {
        "field": "tailscale_ip",
        "value": "100.64.1.9",
        "reason": "input is an IP address",
    }
    assert list(registry_root.iterdir()) == []


def test_host_create_write_materializes_a_valid_directory_manifest(tmp_path: Path) -> None:
    registry_root = _registry_root(tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(registry_root),
            "host",
            "create",
            "--name",
            "new-node.internal",
            "--address",
            "new-node.internal",
            "--write",
        ],
    )

    payload = yaml.safe_load(result.output)
    assert result.exit_code == 0
    assert payload["result"]["mode"] == "written"
    manifest_path = Path(payload["result"]["manifest_path"])
    assert manifest_path == registry_root / payload["result"]["host_id"] / "manifest.yml"
    assert (
        yaml.safe_load(manifest_path.read_text(encoding="utf-8")) == payload["result"]["manifest"]
    )
    created = Registry.load_dir(registry_root).get(payload["result"]["host_id"])
    assert created is not None
    assert created.canonical_name == "new-node.internal"
    assert created.tailscale_name == "new-node.internal"
    assert payload["result"]["address"] == {
        "field": "tailscale_name",
        "value": "new-node.internal",
        "reason": "input is a DNS hostname and maps to tailscale_name",
    }


def test_host_create_rejects_non_directory_registry_and_invalid_address(tmp_path: Path) -> None:
    registry_file = tmp_path / "registry.yml"
    registry_file.write_text("hosts: {}\n", encoding="utf-8")

    non_directory = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(registry_file),
            "host",
            "create",
            "--name",
            "new-node",
            "--address",
            "100.64.1.9",
            "--write",
        ],
    )
    invalid_address = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(_registry_root(tmp_path)),
            "host",
            "create",
            "--name",
            "new-node",
            "--address",
            "not valid",
        ],
    )

    assert non_directory.exit_code == invalid_address.exit_code == 2
    assert yaml.safe_load(non_directory.output)["error"]["code"] == "usage_error"
    assert yaml.safe_load(invalid_address.output)["error"]["code"] == "usage_error"


def test_host_create_is_discoverable_from_generated_host_help() -> None:
    result = CliRunner().invoke(cli, ["help", "host"])

    payload = yaml.safe_load(result.output)
    assert result.exit_code == 0
    children = {item["name"]: item for item in payload["result"]["children"]}
    assert children["create"]["action"] == {
        "rel": "help",
        "command": "infralink help host create",
    }


def test_host_create_refuses_an_existing_generated_uuid_directory(
    monkeypatch, tmp_path: Path
) -> None:
    registry_root = _registry_root(tmp_path)
    host_id = "11111111-1111-4111-8111-111111111111"
    (registry_root / host_id).mkdir()
    monkeypatch.setattr("infralink.cli.main.uuid4", lambda: UUID(host_id))

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(registry_root),
            "host",
            "create",
            "--name",
            "new-node",
            "--address",
            "100.64.1.9",
            "--write",
        ],
    )

    payload = yaml.safe_load(result.output)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"
    assert list((registry_root / host_id).iterdir()) == []
