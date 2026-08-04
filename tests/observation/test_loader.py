from __future__ import annotations

import builtins
import hashlib
import socket
import urllib.request
from pathlib import Path

import pytest

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
    ("source", "code", "pointer"),
    [
        ("applications: []\n", "schema-version-missing", "/schema_version"),
        (
            "schema_version: infralink.observation/v2\n",
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
