import json
import shlex
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator

from infralink.cli.main import cli
from infralink.core.registry import Registry
from infralink.health.checks import HealthCheckResult

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
EDGE_ID = "058e29ff-57b9-47c8-b6fa-0914ac03e25c"
REGISTRY_CHECKOUT: Path
DEFAULT_EDGES: Path


@pytest.fixture(autouse=True)
def _example_checkout(tmp_path: Path) -> None:
    """Materialize the examples as the checkout-root public contract."""
    global REGISTRY_CHECKOUT, DEFAULT_EDGES
    registry = yaml.safe_load((EXAMPLES / "registry.yml").read_text(encoding="utf-8"))
    checkout = tmp_path / "registry"
    for host_id, manifest in registry["hosts"].items():
        path = checkout / "hosts" / host_id / "manifest.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"hosts": {host_id: manifest}}, sort_keys=False), encoding="utf-8"
        )
    edges = checkout / "network" / "main-dev" / "edges" / "edges.yml"
    edges.parent.mkdir(parents=True, exist_ok=True)
    edges.write_text((EXAMPLES / "edges.yml").read_text(encoding="utf-8"), encoding="utf-8")
    REGISTRY_CHECKOUT = checkout
    DEFAULT_EDGES = edges


def invoke(*args: str):
    return CliRunner().invoke(cli, ["--output", "json", *args])


def resolve(*args: str):
    result = invoke(
        "--registry",
        str(REGISTRY_CHECKOUT),
        "--edges",
        str(DEFAULT_EDGES),
        "resolve",
        EDGE_ID,
        *args,
    )
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    return result, json.loads(result.output)


def canary_topology(tmp_path: Path, canary: str) -> tuple[Path, Path]:
    registry = yaml.safe_load((EXAMPLES / "registry.yml").read_text(encoding="utf-8"))
    target = registry["hosts"]["d1b9e5d5-36b0-459d-a556-96622811fbd5"]
    target.setdefault("provider_metadata", {})["password_value"] = canary
    registry_path = tmp_path / "registry"
    for host_id, manifest in registry["hosts"].items():
        path = registry_path / "hosts" / host_id / "manifest.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"hosts": {host_id: manifest}}, sort_keys=False), encoding="utf-8"
        )

    edges_path = tmp_path / "edges.yml"
    edges_path.write_text(DEFAULT_EDGES.read_text(encoding="utf-8"))
    return registry_path, edges_path


def test_resolve_emits_fixed_v1_result_and_source_qualified_actions() -> None:
    result, payload = resolve(
        "--user",
        "reporter+readonly",
        "--database",
        "team/analytics",
        "--prefer-ip",
        "public",
    )

    assert result.exit_code == 0
    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["command"]["parsed"] == {
        "path": ["resolve"],
        "args": {"edge_id": EDGE_ID},
        "flags": [
            "--output",
            "--registry",
            "--edges",
            "--user",
            "--database",
            "--prefer-ip",
        ],
    }
    assert payload["command"]["resolved"]["registry"] == str(REGISTRY_CHECKOUT)
    assert payload["command"]["resolved"]["edges"] == str(DEFAULT_EDGES)
    assert payload["result"] == {
        "edge": {
            "id": EDGE_ID,
            "type": "database",
            "from": {
                "hosts": [
                    "b1a554f8-76ed-4d98-91bb-f0fbfc2818d1",
                    "fa2b9872-d94c-4b20-a73a-57a205560769",
                ],
                "service": "app-worker",
            },
            "to": {
                "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                "service": "postgresql",
                "port": 5432,
            },
            "protocol": "postgresql+psycopg2",
            "secret_ref_count": 1,
            "secret_refs": ["app_postgresql_password"],
            "secret_refs_truncated": False,
        },
        "endpoint": {
            "host": "198.51.100.10",
            "port": 5432,
            "protocol": "postgresql+psycopg2",
        },
        "connection_template": (
            "postgresql+psycopg2://reporter%2Breadonly:"
            "${secret:app_postgresql_password}@198.51.100.10:5432/team%2Fanalytics"
        ),
        "secret_refs": {
            "items": ["app_postgresql_password"],
            "page": {
                "limit": 100,
                "returned": 1,
                "total": 1,
                "next_cursor": None,
            },
        },
    }
    schema = json.loads(
        (ROOT / "src/infralink/schemas/cli/v1/resolve.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(payload)

    actions = {item["rel"]: item for item in payload["next_actions"]}
    check_command = shlex.split(actions["check"]["command"])
    assert check_command[-3:] == ["check", "--edge", EDGE_ID]
    assert actions["check"]["safe"] is True
    assert all("templated" not in item for item in actions.values())


def test_resolve_actions_are_executable_typed_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, payload = resolve()
    monkeypatch.setenv("INFRALINK_REGISTRY", str(REGISTRY_CHECKOUT))
    monkeypatch.setenv("INFRALINK_EDGES", str(DEFAULT_EDGES))
    actions = {item["rel"]: item for item in payload["next_actions"]}
    monkeypatch.setattr(
        "infralink.operator_operations.edge_health.check_edge_health",
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

    replay = invoke(*shlex.split(actions["check"]["command"])[1:])
    assert replay.exit_code == 0, replay.output
    replay_payload = json.loads(replay.output)
    schema = json.loads(
        (ROOT / "src/infralink/schemas/cli/v1/check.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(replay_payload)


@pytest.mark.parametrize(
    "inline",
    [
        True,
        False,
    ],
)
def test_explicit_source_spellings_remain_replayable_in_actions(
    inline: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_args = (
        [f"--registry={REGISTRY_CHECKOUT}", f"--edges={DEFAULT_EDGES}"]
        if inline
        else ["-r", str(REGISTRY_CHECKOUT), "-e", str(DEFAULT_EDGES)]
    )
    monkeypatch.setattr(
        "infralink.operator_operations.edge_health.check_edge_health",
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
    result = invoke(*source_args, "resolve", EDGE_ID)
    payload = json.loads(result.output)

    assert result.exit_code == 0
    action = next(item for item in payload["next_actions"] if item["rel"] == "check")
    assert "--registry" in action["command"]
    assert "--edges" in action["command"]
    replay = invoke(*shlex.split(action["command"])[1:])
    assert replay.exit_code == 0, replay.output


def test_missing_edge_action_preserves_custom_sources_and_is_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path, edges_path = canary_topology(tmp_path, "missing-action-secret")
    result = invoke(
        "--registry",
        str(registry_path),
        "--edges",
        str(edges_path),
        "resolve",
        "absent",
    )
    payload = json.loads(result.output)

    assert result.exit_code == 3
    assert payload["error"]["code"] == "entity_not_found"
    action = payload["next_actions"][0]
    assert shlex.split(action["command"])[-2:] == ["edge", "list"]
    assert "--registry" in action["command"]

    monkeypatch.setenv("INFRALINK_REGISTRY", str(registry_path))
    monkeypatch.setenv("INFRALINK_EDGES", str(edges_path))
    replay = invoke(*shlex.split(action["command"])[1:])
    assert replay.exit_code == 0, replay.output
    replay_payload = json.loads(replay.output)
    schema = json.loads(
        (ROOT / "src/infralink/schemas/cli/v1/edges-list.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(replay_payload)


def test_live_resolve_surface_never_leaks_loaded_secret_value(
    tmp_path: Path,
) -> None:
    canary = "loaded-topology-secret-value-canary"
    registry_path, edges_path = canary_topology(tmp_path, canary)
    loaded = Registry.load_dir(registry_path / "hosts")
    assert (
        loaded.get_by_uuid("d1b9e5d5-36b0-459d-a556-96622811fbd5").provider_metadata[
            "password_value"
        ]
        == canary
    )

    command = cli.get_command(click.Context(cli), "resolve")
    assert command is not None
    option_names = {
        parameter.name
        for parameter in command.params
        if isinstance(parameter, click.Option) and not parameter.name.startswith("_surface_")
    }
    invocations = {
        "default": (),
        "user": ("--user", "reporter"),
        "database": ("--database", "analytics"),
        "prefer_ip:tailscale": ("--prefer-ip", "tailscale"),
        "prefer_ip:public": ("--prefer-ip", "public"),
        "prefer_ip:private": ("--prefer-ip", "private"),
        "missing": ("absent",),
    }
    assert option_names == {"user", "database", "prefer_ip"}

    for name, extra in invocations.items():
        edge_id = EDGE_ID
        options = extra
        if name == "missing":
            edge_id = extra[0]
            options = ()
        result = invoke(
            "--registry",
            str(registry_path),
            "--edges",
            str(edges_path),
            "resolve",
            edge_id,
            *options,
        )
        payload = json.loads(result.output)
        observable = "\n".join(
            (
                result.output,
                result.stderr,
                str(result.exception),
                json.dumps(payload, sort_keys=True),
            )
        )
        assert canary not in observable, name


@pytest.mark.parametrize("prefer_ip", ["tailscale", "public"])
def test_resolve_endpoint_and_template_use_the_same_preferred_ip(prefer_ip: str) -> None:
    _, payload = resolve("--prefer-ip", prefer_ip)
    host = payload["result"]["endpoint"]["host"]
    assert f"@{host}:" in payload["result"]["connection_template"]


@pytest.mark.parametrize("auth_type", ["token", "certificate"])
def test_resolve_never_renders_token_or_certificate_as_uri_password(
    tmp_path: Path, auth_type: str
) -> None:
    edges = yaml.safe_load(DEFAULT_EDGES.read_text(encoding="utf-8"))
    edge = edges["edges"][0]
    edge["auth"] = {"type": auth_type, "secret_ref": "opaque-canary-ref"}
    edge_path = tmp_path / "edges.yml"
    edge_path.write_text(yaml.safe_dump(edges), encoding="utf-8")

    result = invoke(
        "--registry",
        str(REGISTRY_CHECKOUT),
        "--edges",
        str(edge_path),
        "resolve",
        EDGE_ID,
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["connection_template"] is None
    assert payload["result"]["secret_refs"]["items"] == ["opaque-canary-ref"]
    assert "secret value" not in result.output


def test_resolve_rejects_retired_format_as_one_json_usage_error() -> None:
    result, payload = resolve("--format", "url")

    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"


def test_resolve_rejects_password_canary_without_leaking_it() -> None:
    canary = "resolve-canary-secret"
    result, payload = resolve(f"--password={canary}")

    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"
    assert canary not in result.output


def test_resolve_resolution_error_is_safe_and_repairable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver, ResolutionError

    def fail(*args: object, **kwargs: object) -> str:
        raise ResolutionError("provider-canary")

    monkeypatch.setattr(EdgeResolver, "get_target_ip", fail)
    result, payload = resolve()

    assert result.exit_code == 3
    assert payload["error"]["code"] == "input_load_failed"
    assert "provider-canary" not in result.output
    assert payload["next_actions"][0]["command"].endswith("edge list")


def test_resolve_unexpected_error_is_json_internal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver

    def fail(*args: object, **kwargs: object) -> str:
        raise RuntimeError("unexpected-canary")

    monkeypatch.setattr(EdgeResolver, "get_target_ip", fail)
    result, payload = resolve()

    assert result.exit_code == 70
    assert payload["error"]["code"] == "internal_error"
    assert "unexpected-canary" not in result.output
