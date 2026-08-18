from __future__ import annotations

import builtins
import hashlib
import socket
import urllib.request
from pathlib import Path

import pytest

from infralink.observation.diagnostics import SourceLocation
from infralink.observation.loader import load_observation_documents


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_directory_discovery_is_recursive_yaml_only_and_posix_sorted(tmp_path: Path) -> None:
    _write(tmp_path / "z.yaml", "schema_version: infralink.observation/v1\n")
    _write(tmp_path / "a" / "b.yml", "schema_version: infralink.observation/v1\n")
    _write(tmp_path / "ignored.json", "{}")

    report = load_observation_documents(tmp_path)

    assert [document.source_path for document in report.documents] == ["a/b.yml", "z.yaml"]
    assert not report.diagnostics


def test_explicit_relative_file_has_a_relative_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "contract.yml", "schema_version: infralink.observation/v1\n")
    monkeypatch.chdir(tmp_path)

    report = load_observation_documents(Path("contract.yml"))

    assert report.documents[0].source_path == "contract.yml"


def test_raw_and_semantic_digests_have_distinct_stability(tmp_path: Path) -> None:
    path = tmp_path / "contract.yml"
    first = "schema_version: infralink.observation/v1\napplications:\n  - id: mail\n"
    second = "applications: [{id: mail}]\nschema_version: infralink.observation/v1\n"
    _write(path, first)
    first_document = load_observation_documents(path).documents[0]
    _write(path, second)
    second_document = load_observation_documents(path).documents[0]

    assert first_document.raw_sha256 == hashlib.sha256(first.encode()).hexdigest()
    assert first_document.raw_sha256 != second_document.raw_sha256
    assert first_document.semantic_sha256 == second_document.semantic_sha256


def test_loader_accepts_v1_and_v2_documents_without_coercion(tmp_path: Path) -> None:
    _write(tmp_path / "v1.yml", "schema_version: infralink.observation/v1\n")
    _write(tmp_path / "v2.yml", "schema_version: infralink.observation/v2\n")

    report = load_observation_documents(tmp_path)

    assert report.valid
    assert [document.schema_version for document in report.documents] == [
        "infralink.observation/v1",
        "infralink.observation/v2",
    ]


def test_parsed_document_content_is_deeply_immutable_and_can_be_thawed(tmp_path: Path) -> None:
    _write(
        tmp_path / "contract.yml",
        "schema_version: infralink.observation/v1\napplications:\n  - id: mail\n",
    )
    document = load_observation_documents(tmp_path).documents[0]
    digest = document.semantic_sha256

    with pytest.raises(TypeError):
        document.data["schema_version"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        document.data["applications"][0]["id"] = "changed"  # type: ignore[index]

    assert document.semantic_sha256 == digest
    assert document.to_dict()["applications"] == [{"id": "mail"}]


def test_explicit_non_yaml_file_returns_typed_diagnostic(tmp_path: Path) -> None:
    _write(tmp_path / "contract.json", '{"schema_version": "infralink.observation/v1"}')

    report = load_observation_documents(tmp_path / "contract.json")

    assert report.documents == ()
    assert [item.code for item in report.diagnostics] == ["unsupported-source-extension"]
    assert report.diagnostics[0].location.path == "contract.json"


@pytest.mark.parametrize(
    "source",
    [
        "schema_version: infralink.observation/v1\nvalue: &shared [one]\ncopy: *shared\n",
        "schema_version: infralink.observation/v1\nvalue: &cycle [*cycle]\n",
    ],
)
def test_yaml_aliases_and_anchors_are_rejected_before_construction(
    tmp_path: Path, source: str
) -> None:
    _write(tmp_path / "contract.yml", source)

    report = load_observation_documents(tmp_path)

    assert report.documents == ()
    assert [item.code for item in report.diagnostics] == ["yaml-alias-forbidden"]
    assert report.diagnostics[0].location == SourceLocation("contract.yml", "/", 0)


def test_oversized_source_is_rejected_with_typed_diagnostic(tmp_path: Path) -> None:
    from infralink.observation.loader import MAX_SOURCE_BYTES

    _write(tmp_path / "large.yml", "x" * (MAX_SOURCE_BYTES + 1))

    report = load_observation_documents(tmp_path)

    assert report.documents == ()
    assert [item.code for item in report.diagnostics] == ["yaml-source-too-large"]


def test_deeply_nested_yaml_is_rejected_before_construction(tmp_path: Path) -> None:
    from infralink.observation.loader import MAX_YAML_NESTING_DEPTH

    nested = "[" * (MAX_YAML_NESTING_DEPTH + 1) + "value" + "]" * (MAX_YAML_NESTING_DEPTH + 1)
    _write(
        tmp_path / "deep.yml",
        f"schema_version: infralink.observation/v1\nnested: {nested}\n",
    )

    report = load_observation_documents(tmp_path)

    assert report.documents == ()
    assert [item.code for item in report.diagnostics] == ["yaml-nesting-too-deep"]


@pytest.mark.parametrize(
    "source",
    [
        "schema_version: infralink.observation/v1\n1: invalid\n",
        "schema_version: infralink.observation/v1\nnested:\n  - 2: invalid\n",
    ],
)
def test_non_string_mapping_keys_are_rejected_before_hashing(tmp_path: Path, source: str) -> None:
    _write(tmp_path / "contract.yml", source)

    report = load_observation_documents(tmp_path)

    assert report.documents == ()
    assert [item.code for item in report.diagnostics] == ["mapping-key-not-string"]


@pytest.mark.parametrize(
    "source",
    [
        "schema_version: infralink.observation/v1\nunsupported: !!set {one: null}\n",
        "schema_version: infralink.observation/v1\nunsupported: 2026-08-04\n",
        "schema_version: infralink.observation/v1\nunsupported: !!binary SGVsbG8=\n",
        "schema_version: infralink.observation/v1\nunsupported: .nan\n",
        "schema_version: infralink.observation/v1\nunsupported: .inf\n",
    ],
)
def test_values_outside_canonical_domain_are_rejected_before_hashing(
    tmp_path: Path, source: str
) -> None:
    _write(tmp_path / "contract.yml", source)

    report = load_observation_documents(tmp_path)

    assert report.documents == ()
    assert [item.code for item in report.diagnostics] == ["canonical-value-unsupported"]
    assert report.diagnostics[0].location.pointer == "/unsupported"


@pytest.mark.parametrize(
    ("source", "code", "pointer"),
    [
        ("applications: []\n", "schema-version-missing", "/schema_version"),
        (
            "schema_version: infralink.observation/v3\n",
            "schema-version-unsupported",
            "/schema_version",
        ),
        ("schema_version: [broken\n", "yaml-malformed", "/"),
        ("- schema_version: infralink.observation/v1\n", "document-root-not-mapping", "/"),
    ],
)
def test_invalid_documents_return_typed_diagnostics(
    tmp_path: Path, source: str, code: str, pointer: str
) -> None:
    _write(tmp_path / "bad.yml", source)

    report = load_observation_documents(tmp_path)

    assert report.documents == ()
    assert [diagnostic.code for diagnostic in report.diagnostics] == [code]
    assert report.diagnostics[0].location.path == "bad.yml"
    assert report.diagnostics[0].location.pointer == pointer
    assert report.diagnostics[0].next_actions


def test_unknown_top_level_fields_are_retained(tmp_path: Path) -> None:
    _write(
        tmp_path / "contract.yml",
        "schema_version: infralink.observation/v1\nfuture_collection:\n  - id: later\n",
    )

    document = load_observation_documents(tmp_path).documents[0]

    assert document.to_dict()["future_collection"] == [{"id": "later"}]


def test_duplicate_ids_across_documents_report_both_locations(tmp_path: Path) -> None:
    _write(
        tmp_path / "a.yml",
        "schema_version: infralink.observation/v1\napplications:\n  - id: mail\n",
    )
    _write(
        tmp_path / "nested" / "b.yaml",
        "schema_version: infralink.observation/v1\napplications:\n  - id: mail\n",
    )

    report = load_observation_documents(tmp_path)

    duplicates = [d for d in report.diagnostics if d.code == "duplicate-object-id"]
    assert [(d.location.path, d.location.pointer, d.identity) for d in duplicates] == [
        ("a.yml", "/applications/0/id", "applications/mail"),
        ("nested/b.yaml", "/applications/0/id", "applications/mail"),
    ]


def test_duplicate_component_edges_across_v2_documents_report_both_locations(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "a.yml",
        "schema_version: infralink.observation/v2\ncomponent_edges:\n  - id: api-to-db\n",
    )
    _write(
        tmp_path / "b.yml",
        "schema_version: infralink.observation/v2\ncomponent_edges:\n  - id: api-to-db\n",
    )

    report = load_observation_documents(tmp_path)

    duplicates = [d for d in report.diagnostics if d.code == "duplicate-object-id"]
    assert [(d.location.path, d.location.pointer, d.identity) for d in duplicates] == [
        ("a.yml", "/component_edges/0/id", "component_edges/api-to-db"),
        ("b.yml", "/component_edges/0/id", "component_edges/api-to-db"),
    ]


def test_v1_and_v2_collection_ids_do_not_share_identity_namespace(tmp_path: Path) -> None:
    _write(
        tmp_path / "v1.yml",
        "schema_version: infralink.observation/v1\nservice_profiles:\n  - id: nginx\n",
    )
    _write(
        tmp_path / "v2.yml",
        "schema_version: infralink.observation/v2\nservice_profiles:\n  - id: nginx\n",
    )

    report = load_observation_documents(tmp_path)

    assert not [item for item in report.diagnostics if item.code == "duplicate-object-id"]


def test_service_instance_ids_are_scoped_to_their_host(tmp_path: Path) -> None:
    _write(
        tmp_path / "watchtower.yml",
        """schema_version: infralink.observation/v1
service_instances:
  - id: node-exporter
    host_id: 11111111-1111-4111-8111-111111111111
    profile_id: node-exporter
""",
    )
    _write(
        tmp_path / "citadel.yml",
        """schema_version: infralink.observation/v1
service_instances:
  - id: node-exporter
    host_id: 22222222-2222-4222-8222-222222222222
    profile_id: node-exporter
""",
    )

    report = load_observation_documents(tmp_path)

    assert report.valid
    assert not [item for item in report.diagnostics if item.code == "duplicate-object-id"]


def test_duplicate_service_instance_ids_on_one_host_report_both_locations(
    tmp_path: Path,
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    for path in (tmp_path / "a.yml", tmp_path / "nested" / "b.yml"):
        _write(
            path,
            f"""schema_version: infralink.observation/v1
service_instances:
  - id: node-exporter
    host_id: {host_id}
    profile_id: node-exporter
""",
        )

    report = load_observation_documents(tmp_path)

    duplicates = [item for item in report.diagnostics if item.code == "duplicate-object-id"]
    assert [(item.location.path, item.location.pointer, item.identity) for item in duplicates] == [
        (
            "a.yml",
            "/service_instances/0/id",
            f"service_instances/{host_id}/node-exporter",
        ),
        (
            "nested/b.yml",
            "/service_instances/0/id",
            f"service_instances/{host_id}/node-exporter",
        ),
    ]


def test_malformed_instance_hosts_defer_identity_validation_to_the_model(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "missing.yml",
        """schema_version: infralink.observation/v1
service_instances:
  - id: node-exporter
    profile_id: node-exporter
""",
    )
    _write(
        tmp_path / "malformed.yml",
        """schema_version: infralink.observation/v1
service_instances:
  - id: node-exporter
    host_id: [not-a-host]
    profile_id: node-exporter
""",
    )

    report = load_observation_documents(tmp_path)

    assert not [item for item in report.diagnostics if item.code == "duplicate-object-id"]


def test_duplicate_ids_in_same_multi_document_file_have_distinct_locations(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "contracts.yml",
        """schema_version: infralink.observation/v1
applications:
  - id: mail
---
schema_version: infralink.observation/v1
applications:
  - id: mail
""",
    )

    report = load_observation_documents(tmp_path)

    duplicates = [item for item in report.diagnostics if item.code == "duplicate-object-id"]
    assert [item.location.document_index for item in duplicates] == [0, 1]
    assert len({item.location.render() for item in duplicates}) == 2


def test_loading_does_not_read_environment_or_initialize_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "contract.yml", "schema_version: infralink.observation/v1\n")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("ambient access is forbidden")

    monkeypatch.setattr("os.getenv", forbidden)
    monkeypatch.setattr("os.environ.get", forbidden)

    assert len(load_observation_documents(tmp_path).documents) == 1


def test_loading_does_not_access_network_or_import_provider_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "contract.yml", "schema_version: infralink.observation/v1\n")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    real_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("infralink.adapters"):
            raise AssertionError("provider adapter import is forbidden")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert len(load_observation_documents(tmp_path).documents) == 1


def test_diagnostic_limit_is_applied_after_global_sort(tmp_path: Path) -> None:
    for name in ("z.yml", "a.yml", "m.yml"):
        _write(tmp_path / name, "not: [valid\n")

    report = load_observation_documents(tmp_path, diagnostic_limit=2)

    assert [d.location.path for d in report.diagnostics] == ["a.yml", "m.yml"]
    assert report.diagnostics.total_count == 3
    assert report.diagnostics.truncated


def test_zero_diagnostic_limit_does_not_hide_invalid_report(tmp_path: Path) -> None:
    _write(tmp_path / "bad.yml", "not: [valid\n")

    report = load_observation_documents(tmp_path, diagnostic_limit=0)

    assert not report.valid
    assert report.diagnostics.error_count == 1
    assert report.diagnostics.total_count == 1


def test_attempted_document_count_includes_invalid_and_multidocument_sources(
    tmp_path: Path,
) -> None:
    (tmp_path / "multi.yml").write_text(
        """schema_version: infralink.observation/v1
hosts: []
---
- invalid-root
""",
        encoding="utf-8",
    )
    (tmp_path / "malformed.yml").write_text("schema_version: [broken\n", encoding="utf-8")

    report = load_observation_documents(tmp_path)

    assert report.attempted_document_count == 3
    assert len(report.documents) == 1
    assert report.diagnostics.error_count == 2
    assert report.diagnostics.total_count == 2
