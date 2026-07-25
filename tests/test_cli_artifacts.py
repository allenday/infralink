from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from infralink.cli.main import cli
from tests.cli_helpers import assert_schema


def _write_topology(root: Path) -> tuple[Path, Path]:
    registry = root / "registry.yml"
    registry.write_text(
        """
hosts:
  11111111-1111-4111-8111-111111111111:
    canonical_name: alpha
    status: active
    group: core
    roles: [api]
    services:
      metrics:
        port: 9100
        protocol: http
  22222222-2222-4222-8222-222222222222:
    canonical_name: beta
    status: active
    group: data
    roles: [postgres]
""".lstrip(),
        encoding="utf-8",
    )
    edges = root / "edges.yml"
    edges.write_text(
        """
edges:
  - id: aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
    type: database
    from:
      hosts: [11111111-1111-4111-8111-111111111111]
      service: api
    to:
      host: 22222222-2222-4222-8222-222222222222
      service: postgres
      port: 5432
    protocol: postgresql
""".lstrip(),
        encoding="utf-8",
    )
    return registry, edges


def _invoke(root: Path, *args: str):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=root):
        result = runner.invoke(cli, list(args))
        cwd = Path.cwd()
        paths = {
            path.relative_to(cwd): path.read_bytes() for path in cwd.rglob("*") if path.is_file()
        }
    return result, paths


def _payload(result) -> dict:
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    return json.loads(result.output)


@pytest.mark.parametrize("command", ["analyze", "diagram", "docs"])
def test_artifact_commands_require_explicit_output(tmp_path: Path, command: str) -> None:
    registry, edges = _write_topology(tmp_path)
    args = ["--registry", str(registry), "--edges", str(edges), command]
    result = CliRunner().invoke(cli, args)
    payload = _payload(result)

    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"
    assert "--output" in payload["fix"]
    assert payload["command"]["parsed"]["path"] == [command]


def test_diagram_stdout_is_rejected_without_embedded_content(tmp_path: Path) -> None:
    registry, edges = _write_topology(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(registry),
            "--edges",
            str(edges),
            "diagram",
            "--output",
            "generated",
            "--stdout",
        ],
    )
    payload = _payload(result)

    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"
    assert "--output" in payload["fix"]
    assert "content" not in result.output


@pytest.mark.parametrize("command", ["analyze", "diagram", "docs"])
def test_artifact_help_marks_output_required_and_does_not_advertise_stdout(
    command: str,
) -> None:
    result = CliRunner().invoke(cli, ["help", command])
    payload = _payload(result)
    options = {option["name"]: option for option in payload["result"]["options"]}

    assert options["output"]["required"] is True
    assert "stdout" not in options


@pytest.mark.parametrize(
    ("command", "extra", "schema_name", "expected"),
    [
        (
            "analyze",
            (),
            "analyze",
            {
                "registry.yml": "application/yaml",
                "edges.yml": "application/yaml",
                "diagram.mmd": "text/vnd.mermaid",
            },
        ),
        (
            "diagram",
            ("--format", "all"),
            "diagram",
            {
                "infrastructure.md": "text/markdown",
                "infrastructure.d2": "text/vnd.d2",
                "infrastructure.dot": "text/vnd.graphviz",
            },
        ),
        (
            "docs",
            (),
            "docs",
            {
                "index.md": "text/markdown",
                "alpha.md": "text/markdown",
                "beta.md": "text/markdown",
                "edges/index.md": "text/markdown",
            },
        ),
    ],
)
def test_artifact_commands_return_typed_byte_exact_metadata(
    tmp_path: Path,
    command: str,
    extra: tuple[str, ...],
    schema_name: str,
    expected: dict[str, str],
) -> None:
    registry, edges = _write_topology(tmp_path)
    result, files = _invoke(
        tmp_path,
        "--registry",
        str(registry),
        "--edges",
        str(edges),
        command,
        "--output",
        "generated",
        *extra,
    )
    payload = _payload(result)

    assert result.exit_code == 0, result.output
    assert_schema(payload, schema_name)
    artifacts = payload["result"]["artifacts"]
    assert artifacts["page"]["returned"] == len(expected)
    assert artifacts["page"]["total"] == len(expected)
    assert [item["path"] for item in artifacts["items"]] == [
        f"generated/{name}" for name in sorted(expected)
    ]
    assert payload["result"].get("summary", {}).get("artifact_count", len(expected)) == len(
        expected
    )
    assert "content" not in json.dumps(payload)
    for artifact in artifacts["items"]:
        relative = Path(artifact["path"])
        assert not relative.is_absolute()
        assert ".." not in relative.parts
        body = files[relative]
        assert artifact["media_type"] == expected[str(relative.relative_to("generated"))]
        assert artifact["sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("command", ["diagram", "docs"])
def test_topology_load_failure_creates_no_output(tmp_path: Path, command: str) -> None:
    missing = tmp_path / "missing.yml"
    with CliRunner().isolated_filesystem(temp_dir=tmp_path):
        result = CliRunner().invoke(
            cli,
            ["--registry", str(missing), command, "--output", "generated"],
        )
        assert not Path("generated").exists()
    payload = _payload(result)
    assert payload["error"]["code"] == "input_load_failed"


def test_analyze_load_failure_creates_no_output(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.yml"
    malformed.write_text("hosts: [", encoding="utf-8")
    with CliRunner().isolated_filesystem(temp_dir=tmp_path):
        result = CliRunner().invoke(
            cli,
            ["analyze", "--registry", str(malformed), "--output", "generated"],
        )
        assert not Path("generated").exists()
    payload = _payload(result)
    assert payload["error"]["code"] == "input_load_failed"
    assert "hosts: [" not in result.output


def test_input_canary_is_not_disclosed(tmp_path: Path) -> None:
    canary = "artifact-command-canary-secret"
    malformed = tmp_path / "malformed.yml"
    malformed.write_text(f"hosts: [\n  {canary}", encoding="utf-8")
    result = CliRunner().invoke(
        cli,
        ["analyze", "--registry", str(malformed), "--output", "generated"],
    )
    payload = _payload(result)

    assert payload["error"]["code"] == "input_load_failed"
    assert canary not in result.output


@pytest.mark.parametrize("command", ["analyze", "diagram", "docs"])
@pytest.mark.parametrize("output", ["../escaped", "/tmp/infralink-escaped", "."])
def test_artifact_commands_reject_unsafe_output_locations(
    tmp_path: Path, command: str, output: str
) -> None:
    registry, edges = _write_topology(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(registry),
            "--edges",
            str(edges),
            command,
            "--output",
            output,
        ],
    )
    payload = _payload(result)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
@pytest.mark.parametrize("command", ["analyze", "diagram", "docs"])
def test_artifact_commands_reject_preexisting_output_symlink(tmp_path: Path, command: str) -> None:
    registry, edges = _write_topology(tmp_path)
    with CliRunner().isolated_filesystem(temp_dir=tmp_path):
        Path("target").mkdir()
        Path("generated").symlink_to("target", target_is_directory=True)
        result = CliRunner().invoke(
            cli,
            [
                "--registry",
                str(registry),
                "--edges",
                str(edges),
                command,
                "--output",
                "generated",
            ],
        )
        assert list(Path("target").iterdir()) == []
    payload = _payload(result)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_docs_does_not_overwrite_nested_artifact_symlink(tmp_path: Path) -> None:
    registry, edges = _write_topology(tmp_path)
    with CliRunner().isolated_filesystem(temp_dir=tmp_path):
        Path("generated").mkdir()
        Path("outside").write_text("unchanged", encoding="utf-8")
        Path("generated/index.md").symlink_to(Path("outside").resolve())
        result = CliRunner().invoke(
            cli,
            [
                "--registry",
                str(registry),
                "--edges",
                str(edges),
                "docs",
                "--output",
                "generated",
            ],
        )
        assert Path("outside").read_text(encoding="utf-8") == "unchanged"
    payload = _payload(result)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"


def test_artifact_continuation_action_is_executable_and_source_preserving(
    tmp_path: Path,
) -> None:
    registry, edges = _write_topology(tmp_path)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        first = runner.invoke(
            cli,
            [
                "--registry",
                str(registry),
                "--edges",
                str(edges),
                "diagram",
                "--output",
                "generated",
                "--format",
                "all",
                "--limit",
                "1",
            ],
        )
        first_payload = _payload(first)
        continuation = next(
            item for item in first_payload["next_actions"] if item["rel"] == "continue"
        )
        cursor = first_payload["result"]["artifacts"]["page"]["next_cursor"]
        replay = [cursor if item == "{cursor}" else item for item in continuation["argv"]]
        second = runner.invoke(cli, replay[1:])
    second_payload = _payload(second)

    assert second.exit_code == 0
    assert continuation["bindings"]["cursor"]["source"] == ("result.artifacts.page.next_cursor")
    assert continuation["argv"][1:5] == [
        "--registry",
        str(registry),
        "--edges",
        str(edges),
    ]
    assert second_payload["result"]["artifacts"]["page"]["returned"] == 1
    assert (
        second_payload["result"]["artifacts"]["items"][0]["path"]
        != first_payload["result"]["artifacts"]["items"][0]["path"]
    )


def test_analyze_cursor_requires_explicit_collection(tmp_path: Path) -> None:
    registry, _ = _write_topology(tmp_path)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        first = runner.invoke(
            cli,
            [
                "analyze",
                "--registry",
                str(registry),
                "--output",
                "generated",
                "--limit",
                "1",
            ],
        )
        cursor = _payload(first)["result"]["artifacts"]["page"]["next_cursor"]
        replay = runner.invoke(
            cli,
            [
                "analyze",
                "--registry",
                str(registry),
                "--output",
                "generated",
                "--cursor",
                cursor,
                "--limit",
                "1",
            ],
        )
    payload = _payload(replay)
    assert replay.exit_code == 2
    assert payload["error"]["code"] == "invalid_cursor"


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("analyze", ()),
        ("diagram", ("--format", "all")),
        ("docs", ()),
    ],
)
def test_artifact_outputs_are_deterministic(
    tmp_path: Path, command: str, extra: tuple[str, ...]
) -> None:
    registry, edges = _write_topology(tmp_path)
    common = [
        "--registry",
        str(registry),
        "--edges",
        str(edges),
        command,
        "--output",
    ]
    first, first_files = _invoke(tmp_path, *common, "first", *extra)
    second, second_files = _invoke(tmp_path, *common, "second", *extra)
    first_payload = _payload(first)
    second_payload = _payload(second)

    first_bodies = {
        str(path).removeprefix("first/"): body
        for path, body in first_files.items()
        if str(path).startswith("first/")
    }
    second_bodies = {
        str(path).removeprefix("second/"): body
        for path, body in second_files.items()
        if str(path).startswith("second/")
    }
    assert first_bodies == second_bodies
    assert [item["sha256"] for item in first_payload["result"]["artifacts"]["items"]] == [
        item["sha256"] for item in second_payload["result"]["artifacts"]["items"]
    ]
