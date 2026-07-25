from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import infralink.cli.artifacts as artifact_helpers
from infralink.cli.artifacts import write_artifacts
from infralink.cli.errors import CliFailure, ErrorCode
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
    assert continuation["safe"] is False
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
    ("command", "extra", "existing_name"),
    [
        ("analyze", (), "registry.yml"),
        ("diagram", (), "infrastructure.md"),
        ("docs", (), "index.md"),
    ],
)
@pytest.mark.parametrize(
    ("paging_args", "error_code"),
    [
        (("--collection", "absent"), "invalid_cursor"),
        (("--cursor", "bogus"), "invalid_cursor"),
    ],
)
def test_invalid_paging_never_creates_or_overwrites_artifacts(
    tmp_path: Path,
    command: str,
    extra: tuple[str, ...],
    existing_name: str,
    paging_args: tuple[str, ...],
    error_code: str,
) -> None:
    registry, edges = _write_topology(tmp_path)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        output = Path("generated")
        output.mkdir()
        existing = output / existing_name
        existing.write_bytes(b"sentinel")
        result = runner.invoke(
            cli,
            [
                "--registry",
                str(registry),
                "--edges",
                str(edges),
                command,
                "--output",
                str(output),
                *extra,
                *paging_args,
            ],
        )
        assert existing.read_bytes() == b"sentinel"
        assert sorted(path.relative_to(output) for path in output.rglob("*")) == [
            Path(existing_name)
        ]
    payload = _payload(result)
    assert result.exit_code == 2
    assert payload["error"]["code"] == error_code


def test_stale_topology_cursor_does_not_overwrite_artifacts(tmp_path: Path) -> None:
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
        cursor = _payload(first)["result"]["artifacts"]["page"]["next_cursor"]
        protected = Path("generated/infrastructure.md")
        protected.write_bytes(b"sentinel")
        registry.write_text(
            registry.read_text(encoding="utf-8") + "\n# topology changed\n",
            encoding="utf-8",
        )
        stale = runner.invoke(
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
                "--collection",
                "artifacts",
                "--cursor",
                cursor,
            ],
        )
        assert protected.read_bytes() == b"sentinel"
    payload = _payload(stale)
    assert stale.exit_code == 2
    assert payload["error"]["code"] == "invalid_cursor"


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="openat safety unavailable")
def test_descriptor_relative_writer_contains_parent_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output = Path("generated")
    output.mkdir()
    outside = Path("outside")
    outside.mkdir()
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if not swapped and path == "generated" and dir_fd is not None and flags & os.O_DIRECTORY:
            swapped = True
            output.rename("detached")
            output.symlink_to(outside, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(artifact_helpers.os, "open", swapping_open)
    write_artifacts(
        output,
        [(Path("nested/result.txt"), "text/plain", b"bounded")],
    )

    assert swapped is True
    assert list(outside.iterdir()) == []
    assert Path("detached/nested/result.txt").read_bytes() == b"bounded"


def test_analyze_context_resolves_default_root_and_command_override_registry(
    tmp_path: Path,
) -> None:
    from infralink.cli.main import DEFAULT_REGISTRY, _context_for

    root_registry, edges = _write_topology(tmp_path)
    override = tmp_path / "override.yml"
    override.write_text(root_registry.read_text(encoding="utf-8"), encoding="utf-8")
    assert _context_for(path=["analyze"]).resolved["registry"] == DEFAULT_REGISTRY

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        root_result = runner.invoke(
            cli,
            [
                "--registry",
                str(root_registry),
                "--edges",
                str(edges),
                "analyze",
                "--output",
                "root-output",
            ],
        )
        override_result = runner.invoke(
            cli,
            [
                "--registry",
                str(root_registry),
                "--edges",
                str(edges),
                "analyze",
                "--registry",
                str(override),
                "--output",
                "override-output",
            ],
        )

    assert _payload(root_result)["command"]["resolved"]["registry"] == str(root_registry)
    assert _payload(override_result)["command"]["resolved"]["registry"] == str(override)


def test_analyze_invalid_cursor_context_resolves_command_registry_override(
    tmp_path: Path,
) -> None:
    root_registry, edges = _write_topology(tmp_path)
    override = tmp_path / "override.yml"
    override.write_text(root_registry.read_text(encoding="utf-8"), encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(root_registry),
            "--edges",
            str(edges),
            "analyze",
            "--registry",
            str(override),
            "--output",
            "generated",
            "--cursor",
            "bogus",
        ],
    )
    payload = _payload(result)

    assert result.exit_code == 2
    assert payload["error"]["code"] == "invalid_cursor"
    assert payload["command"]["resolved"]["registry"] == str(override)


def test_analyze_input_failure_context_resolves_command_registry_override(
    tmp_path: Path,
) -> None:
    root_registry, edges = _write_topology(tmp_path)
    missing_override = tmp_path / "command-missing.yml"

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(root_registry),
            "--edges",
            str(edges),
            "analyze",
            "--registry",
            str(missing_override),
            "--output",
            "generated",
        ],
    )
    payload = _payload(result)

    assert result.exit_code == 3
    assert payload["error"]["code"] == "input_load_failed"
    assert payload["command"]["resolved"]["registry"] == str(missing_override)


def test_analyze_public_registry_artifact_allowlists_nested_topology(
    tmp_path: Path,
) -> None:
    canary = "nested-artifact-secret-canary"
    registry = tmp_path / "registry.yml"
    registry.write_text(
        f"""
hosts:
  11111111-1111-4111-8111-111111111111:
    canonical_name: useful-host
    status: active
    group: core
    cloud: test-cloud
    roles: [api]
    password: {canary}
    provider_metadata:
      nested:
        token: {canary}
    services:
      api:
        port: 8443
        protocol: https
        password: {canary}
    service_dependencies:
      api:
        - host: 22222222-2222-4222-8222-222222222222
          service: postgres
          port: 5432
          notes: {canary}
ansible_defaults:
  password: {canary}
""".lstrip(),
        encoding="utf-8",
    )
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(
            cli,
            [
                "analyze",
                "--registry",
                str(registry),
                "--output",
                "generated",
            ],
        )
        artifact_bodies = b"\n".join(
            path.read_bytes() for path in Path("generated").rglob("*") if path.is_file()
        )
        public_registry = yaml.safe_load(Path("generated/registry.yml").read_text())
    assert result.exit_code == 0
    assert canary not in result.output
    assert canary.encode() not in artifact_bodies
    host = public_registry["hosts"]["11111111-1111-4111-8111-111111111111"]
    assert host["canonical_name"] == "useful-host"
    assert host["status"] == "active"
    assert host["group"] == "core"
    assert host["roles"] == ["api"]
    assert host["services"]["api"] == {"port": 8443, "protocol": "https"}
    assert "provider_metadata" not in host
    assert "ansible_defaults" not in public_registry


@pytest.mark.parametrize(
    "canonical_names",
    [
        ("duplicate", "duplicate"),
        ("index", "other"),
    ],
)
def test_docs_rejects_duplicate_generated_paths_before_writing(
    tmp_path: Path,
    canonical_names: tuple[str, str],
) -> None:
    registry, edges = _write_topology(tmp_path)
    data = yaml.safe_load(registry.read_text(encoding="utf-8"))
    for host, canonical_name in zip(data["hosts"].values(), canonical_names, strict=True):
        host["canonical_name"] = canonical_name
    registry.write_text(yaml.safe_dump(data), encoding="utf-8")

    with CliRunner().isolated_filesystem(temp_dir=tmp_path):
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
        assert not Path("generated").exists()
    payload = _payload(result)

    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"


def test_artifact_metadata_rejects_duplicate_relative_paths() -> None:
    from infralink.cli.artifacts import artifact_metadata

    generated = [
        (Path("same.txt"), "text/plain", b"first"),
        (Path("same.txt"), "text/plain", b"second"),
    ]

    with pytest.raises(CliFailure) as failure:
        artifact_metadata(Path("generated"), generated)
    assert failure.value.code == ErrorCode.USAGE_ERROR


def test_artifact_transaction_preflight_preserves_earlier_target(
    tmp_path: Path,
) -> None:
    tmp_path.joinpath("generated").mkdir()
    first = tmp_path / "generated/first.txt"
    first.write_bytes(b"original-first")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    (tmp_path / "generated/later.txt").symlink_to(outside)

    with pytest.raises(CliFailure) as failure:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.chdir(tmp_path)
            write_artifacts(
                Path("generated"),
                [
                    (Path("first.txt"), "text/plain", b"new-first"),
                    (Path("later.txt"), "text/plain", b"new-later"),
                ],
            )

    assert failure.value.code == ErrorCode.USAGE_ERROR
    assert first.read_bytes() == b"original-first"
    assert outside.read_bytes() == b"outside"


def test_artifact_transaction_rolls_back_later_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output = Path("generated")
    output.mkdir()
    (output / "first.txt").write_bytes(b"original-first")
    (output / "later.txt").write_bytes(b"original-later")
    real_replace = os.replace

    def failing_replace(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        if destination == "later.txt" and ".tmp-" in str(source):
            raise OSError(errno.EIO, "write-failure-canary")
        return real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(artifact_helpers.os, "replace", failing_replace)
    with pytest.raises(CliFailure) as failure:
        write_artifacts(
            output,
            [
                (Path("first.txt"), "text/plain", b"new-first"),
                (Path("later.txt"), "text/plain", b"new-later"),
            ],
        )

    assert failure.value.code == ErrorCode.INTERNAL_ERROR
    assert "canary" not in failure.value.message
    assert (output / "first.txt").read_bytes() == b"original-first"
    assert (output / "later.txt").read_bytes() == b"original-later"
    assert not list(output.glob(".infralink-*"))


def test_artifact_transaction_cleans_new_directories_after_enospc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    real_open = os.open
    staged = 0

    def failing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal staged
        if flags & os.O_EXCL:
            staged += 1
            if staged == 2:
                raise OSError(errno.ENOSPC, "disk-full-canary")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(artifact_helpers.os, "open", failing_open)
    with pytest.raises(CliFailure) as failure:
        write_artifacts(
            Path("generated"),
            [
                (Path("one/first.txt"), "text/plain", b"first"),
                (Path("two/later.txt"), "text/plain", b"later"),
            ],
        )

    assert failure.value.code == ErrorCode.INTERNAL_ERROR
    assert "canary" not in failure.value.message
    assert not Path("generated").exists()


def test_artifact_transaction_preserves_user_owned_transaction_lookalike(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output = Path("generated")
    output.mkdir()
    stale = output / ".infralink-txn-stale.tmp"
    stale.write_bytes(b"stale")

    write_artifacts(
        output,
        [(Path("result.txt"), "text/plain", b"complete")],
    )

    assert (output / "result.txt").read_bytes() == b"complete"
    assert stale.read_bytes() == b"stale"


def test_artifact_transaction_retains_recovery_state_when_restore_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output = Path("generated")
    output.mkdir()
    (output / "first.txt").write_bytes(b"original-first")
    (output / "later.txt").write_bytes(b"original-later")
    real_replace = os.replace

    def failing_replace(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        if destination == "later.txt" and ".tmp-" in str(source):
            raise OSError(errno.EIO, "commit-failure-canary")
        if ".bak-" in str(source):
            raise OSError(errno.EIO, "restore-failure-canary")
        return real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(artifact_helpers.os, "replace", failing_replace)
    with pytest.raises(CliFailure) as failure:
        write_artifacts(
            output,
            [
                (Path("first.txt"), "text/plain", b"new-first"),
                (Path("later.txt"), "text/plain", b"new-later"),
            ],
        )

    backups = list(output.glob(".infralink-txn-*.bak-*"))
    assert failure.value.code == ErrorCode.INTERNAL_ERROR
    assert failure.value.details == {"recovery_state": "retained"}
    assert ".infralink-recovery.json" in failure.value.fix
    assert "canary" not in failure.value.message
    assert (output / ".infralink-recovery.json").is_file()
    assert {backup.read_bytes() for backup in backups} == {
        b"original-first",
        b"original-later",
    }


def test_artifact_transaction_preserves_existing_recovery_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output = Path("generated")
    output.mkdir()
    target = output / "result.txt"
    target.write_bytes(b"original")
    manifest = output / ".infralink-recovery.json"
    manifest.write_bytes(b"crashed-transaction-state")

    with pytest.raises(CliFailure) as failure:
        write_artifacts(
            output,
            [(Path("result.txt"), "text/plain", b"replacement")],
        )

    assert failure.value.details == {"recovery_state": "retained"}
    assert manifest.read_bytes() == b"crashed-transaction-state"
    assert target.read_bytes() == b"original"


def test_artifact_transaction_syncs_files_and_directories_in_namespace_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output = Path("generated")
    output.mkdir()
    (output / "result.txt").write_bytes(b"original")
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace
    real_unlink = os.unlink

    def recording_fsync(descriptor: int) -> None:
        kind = "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        events.append(f"fsync:{kind}")
        real_fsync(descriptor)

    def recording_replace(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        events.append(f"replace:{source}>{destination}")
        return real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    def recording_unlink(path, *, dir_fd=None):
        events.append(f"unlink:{path}")
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(artifact_helpers.os, "fsync", recording_fsync)
    monkeypatch.setattr(artifact_helpers.os, "replace", recording_replace)
    monkeypatch.setattr(artifact_helpers.os, "unlink", recording_unlink)

    write_artifacts(
        output,
        [(Path("result.txt"), "text/plain", b"replacement")],
    )

    stage_replace = next(
        index
        for index, event in enumerate(events)
        if event.startswith("replace:.infralink-txn-") and event.endswith(">result.txt")
    )
    assert "fsync:file" in events[:stage_replace]
    for index, event in enumerate(events):
        if event.startswith(("replace:", "unlink:")):
            assert events[index + 1] == "fsync:directory"


def test_artifact_transaction_rolls_back_directory_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output = Path("generated")
    output.mkdir()
    target = output / "result.txt"
    target.write_bytes(b"original")
    real_fsync = os.fsync
    real_replace = os.replace
    fail_next_directory_sync = False
    restored_target = False
    restoration_synced = False

    def observing_replace(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        nonlocal fail_next_directory_sync, restored_target
        result = real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if destination == "result.txt" and ".tmp-" in str(source):
            fail_next_directory_sync = True
        if destination == "result.txt" and ".bak-" in str(source):
            restored_target = True
        return result

    def failing_fsync(descriptor: int) -> None:
        nonlocal fail_next_directory_sync, restoration_synced
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if fail_next_directory_sync and is_directory:
            fail_next_directory_sync = False
            raise OSError(errno.EIO, "directory-fsync-canary")
        real_fsync(descriptor)
        if restored_target and is_directory:
            restoration_synced = True

    monkeypatch.setattr(artifact_helpers.os, "replace", observing_replace)
    monkeypatch.setattr(artifact_helpers.os, "fsync", failing_fsync)

    with pytest.raises(CliFailure) as failure:
        write_artifacts(
            output,
            [(Path("result.txt"), "text/plain", b"replacement")],
        )

    assert failure.value.code == ErrorCode.INTERNAL_ERROR
    assert target.read_bytes() == b"original"
    assert restoration_synced
    assert not list(output.glob(".infralink-*"))


def test_artifact_commands_fail_before_mutation_on_unsupported_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, edges = _write_topology(tmp_path)
    monkeypatch.setattr(
        artifact_helpers,
        "_SECURE_ARTIFACT_WRITES_SUPPORTED",
        False,
    )
    with CliRunner().isolated_filesystem(temp_dir=tmp_path):
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
            ],
        )
        assert not Path("generated").exists()
    payload = _payload(result)

    assert result.exit_code == 69
    assert payload["error"]["code"] == "unsupported_platform"
    assert "Linux" in payload["error"]["message"]
    assert "Linux" in payload["fix"]


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
