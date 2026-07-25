from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator

import infralink.cli.secrets as secret_commands
from infralink.adapters.bws import BwsConfigurationError, BwsErrorCode, BwsProviderError
from infralink.cli.main import cli
from infralink.secrets import SecretAudit

ROOT = Path(__file__).resolve().parents[1]
HOST_A = "11111111-1111-4111-8111-111111111111"
HOST_B = "22222222-2222-4222-8222-222222222222"
PROJECT_A = "33333333-3333-4333-8333-333333333333"
PROJECT_B = "44444444-4444-4444-8444-444444444444"


def write_topology(
    tmp_path: Path,
    declarations: list[tuple[str, str, str]],
) -> tuple[Path, Path]:
    registry_path = tmp_path / "registry.yml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "hosts": {
                    HOST_A: {
                        "canonical_name": "a.example",
                        "bws_project": PROJECT_A,
                    },
                    HOST_B: {
                        "canonical_name": "b.example",
                        "bws_project": PROJECT_B,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    edges = []
    for index, (target, ref, edge_id) in enumerate(declarations):
        edges.append(
            {
                "id": edge_id,
                "type": "database",
                "from": {"hosts": [], "service": f"app-{index}"},
                "to": {"host": target, "service": "postgres", "port": 5432},
                "auth": {"type": "password", "secret_ref": ref},
            }
        )
    edges_path = tmp_path / "edges.yml"
    edges_path.write_text(yaml.safe_dump({"edges": edges}), encoding="utf-8")
    return registry_path, edges_path


def invoke(registry: Path, edges: Path, *args: str):
    return CliRunner().invoke(
        cli,
        ["--registry", str(registry), "--edges", str(edges), *args],
    )


def payload(result) -> dict:
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    return json.loads(result.output)


def source(registry: Path, edges: Path) -> list[str]:
    return [
        "infralink",
        "--registry",
        str(registry),
        "--edges",
        str(edges),
    ]


def test_inspect_is_offline_bounded_and_schema_valid(tmp_path: Path) -> None:
    declarations = [
        (HOST_A, "shared", f"00000000-0000-4000-8000-{index:012d}") for index in range(18)
    ]
    registry, edges = write_topology(tmp_path, declarations)

    result = invoke(registry, edges, "secrets", "inspect", "--limit", "1")
    body = payload(result)

    assert result.exit_code == 0
    assert body["result"]["summary"] == {
        "total": 1,
        "present": 0,
        "missing": 0,
        "accessible": 0,
        "denied": 0,
    }
    reference = body["result"]["references"]["items"][0]
    assert reference["ref"] == "shared"
    assert reference["location_count"] == 18
    assert len(reference["location_preview"]) == 16
    assert reference["locations_truncated"] is True
    assert reference["present"] is None
    assert reference["accessible"] is None
    assert reference["error_code"] is None
    assert body["result"]["locations"]["items"] == []
    assert body["meta"]["truncated"] is True
    escalation = next(item for item in body["next_actions"] if item["rel"] == "inspect")
    assert escalation["argv"] == [
        *source(registry, edges),
        "secrets",
        "inspect",
        "--ref",
        "shared",
        "--collection",
        "locations",
    ]
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1/secrets-inspect.json").read_text())
    Draft202012Validator(schema).validate(body)


def test_inspect_ref_unions_locations_across_projects_and_pages(tmp_path: Path) -> None:
    declarations = [
        (HOST_A, "shared", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        (HOST_B, "shared", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    ]
    registry, edges = write_topology(tmp_path, declarations)

    first = payload(
        invoke(
            registry,
            edges,
            "secrets",
            "inspect",
            "--ref",
            "shared",
            "--collection",
            "locations",
            "--limit",
            "1",
        )
    )

    assert [item["project"] for item in first["result"]["references"]["items"]] == [PROJECT_A]
    assert first["result"]["references"]["page"]["total"] == 2
    assert first["result"]["locations"]["page"]["total"] == 2
    continuation = next(
        item
        for item in first["next_actions"]
        if item["bindings"].get("cursor", {}).get("source") == "result.locations.page.next_cursor"
    )
    assert continuation["argv"][:9] == [
        *source(registry, edges),
        "secrets",
        "inspect",
        "--ref",
        "shared",
    ]
    cursor = first["result"]["locations"]["page"]["next_cursor"]
    replay_argv = [cursor if item == "{cursor}" else item for item in continuation["argv"]]
    second = CliRunner().invoke(cli, replay_argv[1:])
    second_body = payload(second)
    assert second_body["result"]["locations"]["page"]["returned"] == 1
    assert second_body["result"]["locations"]["page"]["next_cursor"] is None


def test_unknown_ref_is_source_qualified_entity_failure(tmp_path: Path) -> None:
    registry, edges = write_topology(tmp_path, [])
    result = invoke(registry, edges, "secrets", "inspect", "--ref", "absent")
    body = payload(result)

    assert result.exit_code == 3
    assert body["error"]["code"] == "entity_not_found"
    assert body["error"]["details"] == {
        "entity_type": "secret_reference",
        "requested_id": "absent",
    }
    assert body["next_actions"][0]["argv"] == [
        *source(registry, edges),
        "secrets",
        "inspect",
    ]


class FakeResolver:
    def __init__(self, audits: list[SecretAudit]) -> None:
        self.audits = audits
        self.seen = []

    def audit(self, references):
        self.seen = references
        return self.audits


def test_audit_joins_by_ref_and_project_and_returns_exit_one_for_negative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    declarations = [
        (HOST_A, "shared", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        (HOST_B, "shared", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    ]
    registry, edges = write_topology(tmp_path, declarations)
    resolver = FakeResolver(
        [
            SecretAudit("shared", PROJECT_B, None, False, "unavailable_or_missing"),
            SecretAudit("shared", PROJECT_A, True, True),
        ]
    )
    monkeypatch.setattr(secret_commands, "_build_bws_resolver", lambda: resolver)

    result = invoke(
        registry,
        edges,
        "secrets",
        "audit",
        "--provider",
        "bws",
        "--ref",
        "shared",
    )
    body = payload(result)

    assert result.exit_code == 1
    assert body["ok"] is True
    assert body["command"]["resolved"]["provider"] == "bws"
    assert [item["project"] for item in body["result"]["references"]["items"]] == [
        PROJECT_A,
        PROJECT_B,
    ]
    assert body["result"]["summary"] == {
        "total": 2,
        "present": 1,
        "missing": 1,
        "accessible": 1,
        "denied": 1,
    }
    assert all(len(item["location_preview"]) == 1 for item in body["result"]["references"]["items"])
    assert [item.project for item in resolver.seen] == [PROJECT_A, PROJECT_B]


def test_audit_canonicalizes_uppercase_topology_project_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, edges = write_topology(
        tmp_path,
        [(HOST_A, "shared", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
    )
    raw_registry = yaml.safe_load(registry.read_text(encoding="utf-8"))
    uppercase_project = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    canonical_project = uppercase_project.lower()
    raw_registry["hosts"][HOST_A]["bws_project"] = uppercase_project
    registry.write_text(yaml.safe_dump(raw_registry), encoding="utf-8")
    resolver = FakeResolver([SecretAudit("shared", canonical_project, True, True)])
    monkeypatch.setattr(secret_commands, "_build_bws_resolver", lambda: resolver)

    result = invoke(registry, edges, "secrets", "audit", "--provider", "bws")
    body = payload(result)

    assert result.exit_code == 0
    assert [item.project for item in resolver.seen] == [canonical_project]
    assert body["result"]["references"]["items"][0]["project"] == canonical_project


def test_audit_coalesces_references_that_share_a_canonical_project_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, edges = write_topology(
        tmp_path,
        [
            (HOST_A, "shared", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            (HOST_B, "shared", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ],
    )
    uppercase_project = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    canonical_project = uppercase_project.lower()
    raw_registry = yaml.safe_load(registry.read_text(encoding="utf-8"))
    raw_registry["hosts"][HOST_A]["bws_project"] = uppercase_project
    raw_registry["hosts"][HOST_B]["bws_project"] = canonical_project
    registry.write_text(yaml.safe_dump(raw_registry), encoding="utf-8")
    resolver = FakeResolver([SecretAudit("shared", canonical_project, True, True)])
    monkeypatch.setattr(secret_commands, "_build_bws_resolver", lambda: resolver)

    result = invoke(registry, edges, "secrets", "audit", "--provider", "bws")
    body = payload(result)

    assert result.exit_code == 0
    assert len(resolver.seen) == 1
    assert resolver.seen[0].project == canonical_project
    assert resolver.seen[0].locations == (
        "edges.aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.auth.secret_ref",
        "edges.bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.auth.secret_ref",
    )
    assert body["result"]["summary"]["total"] == 1
    reference = body["result"]["references"]["items"][0]
    assert reference["project"] == canonical_project
    assert reference["location_count"] == 2
    assert [item["path"] for item in reference["location_preview"]] == list(
        resolver.seen[0].locations
    )


def test_empty_audit_does_not_construct_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, edges = write_topology(tmp_path, [])
    monkeypatch.setattr(
        secret_commands,
        "_build_bws_resolver",
        lambda: pytest.fail("provider should not be constructed"),
    )

    result = invoke(registry, edges, "secrets", "audit", "--provider", "bws")
    body = payload(result)

    assert result.exit_code == 0
    assert body["result"]["references"]["items"] == []


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (ModuleNotFoundError("bitwarden_sdk"), "provider_unavailable"),
        (BwsProviderError(BwsErrorCode.PROVIDER_TIMEOUT), "provider_timeout"),
        (
            BwsProviderError(BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED),
            "provider_authentication_failed",
        ),
    ],
)
def test_provider_failures_are_safe_and_repair_oriented(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    code: str,
) -> None:
    registry, edges = write_topology(
        tmp_path,
        [(HOST_A, "shared", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
    )
    monkeypatch.setattr(
        secret_commands,
        "_build_bws_resolver",
        lambda: (_ for _ in ()).throw(failure),
    )

    result = invoke(registry, edges, "secrets", "audit", "--provider", "bws")
    body = payload(result)

    assert result.exit_code == 4
    assert body["error"]["code"] == code
    if isinstance(failure, ModuleNotFoundError):
        install = next(item for item in body["next_actions"] if item["rel"] == "install")
        assert install["argv"] == [
            "python",
            "-m",
            "pip",
            "install",
            "infralink[bws]",
        ]
        assert install["safe"] is False


def test_missing_provider_configuration_is_authentication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, edges = write_topology(
        tmp_path,
        [(HOST_A, "shared", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
    )
    monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BWS_ORGANIZATION_ID", raising=False)
    monkeypatch.setattr(
        secret_commands,
        "_build_bws_resolver",
        lambda: (_ for _ in ()).throw(BwsConfigurationError("canary-secret")),
    )

    result = invoke(registry, edges, "secrets", "audit", "--provider", "bws")
    body = payload(result)

    assert result.exit_code == 4
    assert body["error"]["code"] == "provider_authentication_failed"
    assert "canary-secret" not in result.output


def test_invalid_declared_project_is_provider_unavailable_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, edges = write_topology(
        tmp_path,
        [(HOST_A, "shared", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
    )
    raw_registry = yaml.safe_load(registry.read_text(encoding="utf-8"))
    raw_registry["hosts"][HOST_A]["bws_project"] = "invalid-project-canary"
    registry.write_text(yaml.safe_dump(raw_registry), encoding="utf-8")
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "opaque-token-canary")
    monkeypatch.setenv("BWS_ORGANIZATION_ID", PROJECT_B)
    monkeypatch.setattr(
        secret_commands,
        "_build_bws_resolver",
        lambda: SimpleNamespace(
            audit=lambda references: (_ for _ in ()).throw(
                BwsConfigurationError("invalid-project-canary")
            )
        ),
    )

    result = invoke(registry, edges, "secrets", "audit", "--provider", "bws")
    body = payload(result)

    assert result.exit_code == 4
    assert body["error"]["code"] == "provider_unavailable"
    assert "invalid-project-canary" not in result.output
    assert "opaque-token-canary" not in result.output


@pytest.mark.parametrize(
    "audits",
    [
        [SecretAudit("wrong", PROJECT_A, True, True)],
        [],
        [
            SecretAudit("shared", PROJECT_A, True, True),
            SecretAudit("shared", PROJECT_A, True, True),
        ],
    ],
    ids=["identity-mismatch", "missing", "duplicate"],
)
def test_audit_rejects_malformed_provider_results_without_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audits: list[SecretAudit],
) -> None:
    registry, edges = write_topology(
        tmp_path,
        [(HOST_A, "shared", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")],
    )
    resolver = FakeResolver(audits)
    monkeypatch.setattr(secret_commands, "_build_bws_resolver", lambda: resolver)

    result = invoke(registry, edges, "secrets", "audit", "--provider", "bws")
    body = payload(result)

    assert result.exit_code == 4
    assert body["error"]["code"] == "provider_unavailable"
    assert "result" not in body


def test_secrets_cursor_is_bound_to_command_collection_ref_and_topology(
    tmp_path: Path,
) -> None:
    registry, edges = write_topology(
        tmp_path,
        [
            (HOST_A, "shared", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            (HOST_B, "shared", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ],
    )
    first = payload(
        invoke(
            registry,
            edges,
            "secrets",
            "inspect",
            "--ref",
            "shared",
            "--collection",
            "locations",
            "--limit",
            "1",
        )
    )
    cursor = first["result"]["locations"]["page"]["next_cursor"]
    assert cursor

    attempts = [
        (
            "inspect",
            "--ref",
            "other",
            "--collection",
            "locations",
            "--cursor",
            cursor,
        ),
        (
            "inspect",
            "--ref",
            "shared",
            "--collection",
            "references",
            "--cursor",
            cursor,
        ),
        (
            "audit",
            "--provider",
            "bws",
            "--collection",
            "references",
            "--cursor",
            cursor,
        ),
        (
            "inspect",
            "--ref",
            "shared",
            "--collection",
            "locations",
            "--cursor",
            f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}",
        ),
    ]
    for args in attempts:
        result = invoke(registry, edges, "secrets", *args)
        body = payload(result)
        assert result.exit_code == 2
        assert body["error"]["code"] == "invalid_cursor"

    raw_edges = yaml.safe_load(edges.read_text(encoding="utf-8"))
    raw_edges["edges"].append(
        {
            "id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "type": "database",
            "from": {"hosts": [], "service": "new"},
            "to": {"host": HOST_A, "service": "postgres", "port": 5432},
            "auth": {"type": "password", "secret_ref": "shared"},
        }
    )
    edges.write_text(yaml.safe_dump(raw_edges), encoding="utf-8")
    stale = invoke(
        registry,
        edges,
        "secrets",
        "inspect",
        "--ref",
        "shared",
        "--collection",
        "locations",
        "--cursor",
        cursor,
    )
    assert stale.exit_code == 2
    assert payload(stale)["error"]["code"] == "invalid_cursor"


@pytest.mark.parametrize(
    "forbidden",
    [
        ("--token", "credential-value-canary"),
        ("--project", PROJECT_A),
        ("--secret-id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    ],
)
def test_audit_rejects_credential_and_arbitrary_identity_options(
    tmp_path: Path, forbidden: tuple[str, str]
) -> None:
    registry, edges = write_topology(tmp_path, [])
    result = invoke(registry, edges, "secrets", "audit", *forbidden)
    body = payload(result)
    assert result.exit_code == 2
    assert body["error"]["code"] == "usage_error"
    if forbidden[0] == "--token":
        assert forbidden[1] not in result.output


def test_secrets_help_is_live_and_has_no_credential_options() -> None:
    inspect = payload(CliRunner().invoke(cli, ["help", "secrets", "inspect"]))
    audit = payload(CliRunner().invoke(cli, ["help", "secrets", "audit"]))

    assert {item["name"] for item in inspect["result"]["options"]} == {
        "ref",
        "limit",
        "cursor",
        "collection",
    }
    assert {item["name"] for item in audit["result"]["options"]} == {
        "provider",
        "ref",
        "limit",
        "cursor",
        "collection",
    }
    assert "unavailable" not in inspect["result"]["description"].lower()


def test_root_discovery_does_not_import_optional_sdk() -> None:
    script = """
import builtins
real_import = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "bitwarden_sdk":
        raise AssertionError("optional SDK imported during root discovery")
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded
from infralink.cli.main import main
raise SystemExit(main([]))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True


def test_audit_schema_and_provider_paging_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, edges = write_topology(
        tmp_path,
        [
            (HOST_A, "alpha", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            (HOST_A, "beta", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ],
    )

    def factory():
        return SimpleNamespace(
            audit=lambda references: [
                SecretAudit(item.ref, item.project, True, True) for item in references
            ]
        )

    monkeypatch.setattr(secret_commands, "_build_bws_resolver", factory)
    first = payload(
        invoke(
            registry,
            edges,
            "secrets",
            "audit",
            "--provider",
            "bws",
            "--limit",
            "1",
        )
    )
    action = next(item for item in first["next_actions"] if item["rel"] == "continue")
    provider_index = action["argv"].index("--provider")
    assert ["--provider", "bws"] == action["argv"][provider_index : provider_index + 2]
    cursor = first["result"]["references"]["page"]["next_cursor"]
    replay = [cursor if item == "{cursor}" else item for item in action["argv"]]
    second = payload(CliRunner().invoke(cli, replay[1:]))
    assert second["result"]["references"]["items"][0]["ref"] == "beta"
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1/secrets-audit.json").read_text())
    Draft202012Validator(schema).validate(first)
