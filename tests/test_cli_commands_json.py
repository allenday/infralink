import json
from pathlib import Path

from click.testing import CliRunner

from infralink.cli.main import cli


def _write_registry(path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            """
hosts:
  11111111-1111-1111-1111-111111111111:
    canonical_name: test-host
    status: active
    tailscale_ip: 100.0.0.1
"""
        )


def _write_edges(path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            """
edges:
  - id: 22222222-2222-2222-2222-222222222222
    type: database
    protocol: postgresql
    from:
      hosts: [11111111-1111-1111-1111-111111111111]
    to:
      host: 11111111-1111-1111-1111-111111111111
      service: postgresql
      port: 5432
"""
        )


def test_info_json():
    runner = CliRunner()
    with runner.isolated_filesystem():
        _write_registry("registry.yml")
        _write_edges("edges.yml")
        result = runner.invoke(cli, ["--registry", "registry.yml", "--edges", "edges.yml", "info"])
    payload = json.loads(result.output)
    assert payload["ok"] is True


def test_hosts_json():
    runner = CliRunner()
    with runner.isolated_filesystem():
        _write_registry("registry.yml")
        result = runner.invoke(cli, ["--registry", "registry.yml", "hosts"])
    payload = json.loads(result.output)
    assert payload["ok"] is True


def test_edges_list_json():
    runner = CliRunner()
    with runner.isolated_filesystem():
        _write_registry("registry.yml")
        _write_edges("edges.yml")
        result = runner.invoke(
            cli, ["--registry", "registry.yml", "--edges", "edges.yml", "edges-list"]
        )
    payload = json.loads(result.output)
    assert payload["ok"] is True


def test_diagram_stdout_is_a_json_usage_error():
    runner = CliRunner()

    with runner.isolated_filesystem():
        _write_registry("registry.yml")
        _write_edges("edges.yml")

        result = runner.invoke(
            cli,
            [
                "--registry",
                "registry.yml",
                "--edges",
                "edges.yml",
                "diagram",
                "--format",
                "d2",
                "--output",
                "generated",
                "--stdout",
            ],
        )
        assert not Path("generated").exists()

    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "usage_error"
    assert "--output" in payload["fix"]
    assert "content" not in result.output
