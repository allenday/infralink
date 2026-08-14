from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

import infralink.cli.registry_authoring as registry_authoring
from infralink.cli.main import cli


def _registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "hosts"
    manifest = root / "11111111-1111-4111-8111-111111111111" / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        """
hosts:
  11111111-1111-4111-8111-111111111111:
    canonical_name: alpha
    status: provisioning
    tailscale_ip: 100.64.1.9
    controller_bootstrap:
      controller_image: ghcr.io/example/controller:main
      bootstrap_note: initial note
      pull_enabled: false
""".lstrip(),
        encoding="utf-8",
    )
    return root


def _payload(result) -> dict:
    assert result.stderr == ""
    return yaml.safe_load(result.output)


def test_registry_host_get_returns_the_authoritative_manifest_location(tmp_path: Path) -> None:
    root = _registry_root(tmp_path)

    result = CliRunner().invoke(cli, ["--registry", str(root), "registry", "host", "get", "alpha"])

    payload = _payload(result)
    assert result.exit_code == 0, result.output
    assert payload["result"]["host"]["id"] == "11111111-1111-4111-8111-111111111111"
    assert payload["result"]["manifest_path"] == str(
        root / "11111111-1111-4111-8111-111111111111" / "manifest.yml"
    )
    assert payload["result"]["declaration"]["controller_bootstrap"]["controller_image"] == (
        "ghcr.io/example/controller:main"
    )
    assert any(item["rel"] == "patch" for item in payload["next_actions"])
    patch = next(item for item in payload["next_actions"] if item["rel"] == "patch")
    assert patch["templated"] is True
    assert patch["bindings"] == {
        "path": {"type": "string", "required": True, "source": "operator.input"},
        "value": {
            "type": "string",
            "required": True,
            "source": "operator.input",
            "syntax": "YAML_VALUE | @text:FILE | @yaml:FILE",
            "examples": ["ghcr.io/example/controller:v0.5.5", "@text:FILE", "@yaml:FILE"],
        },
    }


def test_registry_host_get_redacts_secret_shaped_declaration_values(tmp_path: Path) -> None:
    root = _registry_root(tmp_path)
    manifest = root / "11111111-1111-4111-8111-111111111111" / "manifest.yml"
    manifest.write_text(
        f"{manifest.read_text(encoding='utf-8')}    provider_metadata:\n      password_value: do-not-expose\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["--registry", str(root), "registry", "host", "get", "alpha"])

    payload = _payload(result)
    assert result.exit_code == 0, result.output
    assert "do-not-expose" not in result.output
    assert payload["result"]["declaration"]["provider_metadata"]["password_value"] == "[REDACTED]"


def test_registry_host_patch_previews_then_writes_a_typed_dot_addressed_mutation(
    tmp_path: Path,
) -> None:
    root = _registry_root(tmp_path)
    command = [
        "--registry",
        str(root),
        "registry",
        "host",
        "patch",
        "alpha",
        "--set",
        "controller_bootstrap.controller_image=ghcr.io/example/controller:v0.5.5",
    ]

    preview = CliRunner().invoke(cli, command)
    preview_payload = _payload(preview)
    assert preview.exit_code == 0, preview.output
    assert preview_payload["result"]["mode"] == "preview"
    assert preview_payload["result"]["changes"] == [
        {
            "path": "controller_bootstrap.controller_image",
            "before": "ghcr.io/example/controller:main",
            "after": "ghcr.io/example/controller:v0.5.5",
        }
    ]
    assert (
        yaml.safe_load(
            (root / "11111111-1111-4111-8111-111111111111" / "manifest.yml").read_text(
                encoding="utf-8"
            )
        )["hosts"]["11111111-1111-4111-8111-111111111111"]["controller_bootstrap"][
            "controller_image"
        ]
        == "ghcr.io/example/controller:main"
    )
    assert any(item["rel"] == "write" for item in preview_payload["next_actions"])

    applied = CliRunner().invoke(cli, [*command, "--write"])
    applied_payload = _payload(applied)
    assert applied.exit_code == 0, applied.output
    assert applied_payload["result"]["mode"] == "written"
    assert (
        yaml.safe_load(
            (root / "11111111-1111-4111-8111-111111111111" / "manifest.yml").read_text(
                encoding="utf-8"
            )
        )["hosts"]["11111111-1111-4111-8111-111111111111"]["controller_bootstrap"][
            "controller_image"
        ]
        == "ghcr.io/example/controller:v0.5.5"
    )


def test_registry_host_patch_reads_literal_multiline_text_from_an_explicit_file_source(
    tmp_path: Path,
) -> None:
    root = _registry_root(tmp_path)
    source = tmp_path / "bootstrap-note.txt"
    source.write_text("first line\nsecond line\n", encoding="utf-8")
    command = [
        "--registry",
        str(root),
        "registry",
        "host",
        "patch",
        "alpha",
        "--set",
        f"controller_bootstrap.bootstrap_note=@text:{source}",
    ]

    preview = CliRunner().invoke(cli, command)
    preview_payload = _payload(preview)
    assert preview.exit_code == 0, preview.output
    assert preview_payload["result"]["changes"][0]["after"] == "first line\nsecond line\n"

    applied = CliRunner().invoke(cli, [*command, "--write"])
    assert applied.exit_code == 0, applied.output
    document = yaml.safe_load(
        (root / "11111111-1111-4111-8111-111111111111" / "manifest.yml").read_text(encoding="utf-8")
    )
    assert (
        document["hosts"]["11111111-1111-4111-8111-111111111111"]["controller_bootstrap"][
            "bootstrap_note"
        ]
        == "first line\nsecond line\n"
    )


def test_registry_host_patch_help_documents_explicit_file_source_grammars() -> None:
    result = CliRunner().invoke(cli, ["help", "registry", "host", "patch"])

    payload = _payload(result)
    assert result.exit_code == 0, result.output
    assert "@text:FILE" in payload["result"]["examples"][0]
    assert "@yaml:FILE" in payload["result"]["examples"][1]


def test_registry_host_patch_reads_a_typed_yaml_value_from_an_explicit_file_source(
    tmp_path: Path,
) -> None:
    root = _registry_root(tmp_path)
    source = tmp_path / "pull-enabled.yml"
    source.write_text("true\n", encoding="utf-8")
    command = [
        "--registry",
        str(root),
        "registry",
        "host",
        "patch",
        "alpha",
        "--set",
        f"controller_bootstrap.pull_enabled=@yaml:{source}",
    ]

    preview = CliRunner().invoke(cli, command)
    preview_payload = _payload(preview)
    assert preview.exit_code == 0, preview.output
    assert preview_payload["result"]["changes"][0]["after"] is True

    applied = CliRunner().invoke(cli, [*command, "--write"])
    assert applied.exit_code == 0, applied.output
    document = yaml.safe_load(
        (root / "11111111-1111-4111-8111-111111111111" / "manifest.yml").read_text(encoding="utf-8")
    )
    assert (
        document["hosts"]["11111111-1111-4111-8111-111111111111"]["controller_bootstrap"][
            "pull_enabled"
        ]
        is True
    )


def test_registry_host_patch_rejects_an_unreadable_explicit_file_source_without_writing(
    tmp_path: Path,
) -> None:
    root = _registry_root(tmp_path)
    manifest = root / "11111111-1111-4111-8111-111111111111" / "manifest.yml"
    original = manifest.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(root),
            "registry",
            "host",
            "patch",
            "alpha",
            "--set",
            f"controller_bootstrap.bootstrap_note=@text:{tmp_path / 'missing.txt'}",
            "--write",
        ],
    )

    payload = _payload(result)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"
    assert "file source" in payload["error"]["message"]
    assert manifest.read_text(encoding="utf-8") == original


def test_registry_host_patch_rejects_unknown_parent_paths_without_writing(tmp_path: Path) -> None:
    root = _registry_root(tmp_path)
    manifest = root / "11111111-1111-4111-8111-111111111111" / "manifest.yml"
    original = manifest.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(root),
            "registry",
            "host",
            "patch",
            "alpha",
            "--set",
            "controller_bootstrap.controller_imgae=ghcr.io/example/controller:v0.5.5",
            "--write",
        ],
    )

    payload = _payload(result)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"
    assert manifest.read_text(encoding="utf-8") == original


def test_registry_host_patch_preserves_unrelated_yaml_comments_and_flow_style(
    tmp_path: Path,
) -> None:
    root = _registry_root(tmp_path)
    manifest = root / "11111111-1111-4111-8111-111111111111" / "manifest.yml"
    manifest.write_text(
        """
# operator note
hosts:
  11111111-1111-4111-8111-111111111111:
    canonical_name: alpha
    status: provisioning
    tailscale_ip: 100.64.1.9
    controller_bootstrap: {controller_image: ghcr.io/example/controller:main}
""".lstrip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(root),
            "registry",
            "host",
            "patch",
            "alpha",
            "--set",
            "controller_bootstrap.controller_image=ghcr.io/example/controller:v0.5.5",
            "--write",
        ],
    )

    assert result.exit_code == 0, result.output
    rendered = manifest.read_text(encoding="utf-8")
    assert rendered.startswith("# operator note\n")
    assert "controller_bootstrap: {controller_image: ghcr.io/example/controller:v0.5.5}" in rendered


def test_registry_host_patch_refuses_a_managed_runtime_checkout(
    monkeypatch, tmp_path: Path
) -> None:
    root = _registry_root(tmp_path)
    monkeypatch.setattr(registry_authoring, "_managed_runtime_registry_root", lambda: root)

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(root),
            "registry",
            "host",
            "patch",
            "alpha",
            "--set",
            "controller_bootstrap.controller_image=ghcr.io/example/controller:v0.5.5",
            "--write",
        ],
    )

    payload = _payload(result)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"
    assert "managed runtime checkout" in payload["error"]["message"]


def test_registry_host_patch_rejects_alias_backed_fields_without_writing(tmp_path: Path) -> None:
    root = _registry_root(tmp_path)
    manifest = root / "11111111-1111-4111-8111-111111111111" / "manifest.yml"
    manifest.write_text(
        """
controller_default: &controller ghcr.io/example/controller:main
hosts:
  11111111-1111-4111-8111-111111111111:
    canonical_name: alpha
    status: provisioning
    tailscale_ip: 100.64.1.9
    controller_bootstrap:
      controller_image: *controller
""".lstrip(),
        encoding="utf-8",
    )
    original = manifest.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(root),
            "registry",
            "host",
            "patch",
            "alpha",
            "--set",
            "controller_bootstrap.controller_image=ghcr.io/example/controller:v0.5.5",
            "--write",
        ],
    )

    payload = _payload(result)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"
    assert "alias-backed" in payload["error"]["message"]
    assert manifest.read_text(encoding="utf-8") == original


def test_registry_host_patch_rejects_duplicate_paths_during_preview(tmp_path: Path) -> None:
    root = _registry_root(tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(root),
            "registry",
            "host",
            "patch",
            "alpha",
            "--set",
            "controller_bootstrap.controller_image=ghcr.io/example/controller:v0.5.5",
            "--set",
            "controller_bootstrap.controller_image=ghcr.io/example/controller:v0.5.6",
        ],
    )

    payload = _payload(result)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"
    assert "unique" in payload["error"]["message"]
