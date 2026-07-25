from pathlib import Path

import tomllib

import infralink

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_pyproject() -> dict[str, object]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        return tomllib.load(pyproject_file)


def test_package_version_is_0_2_0() -> None:
    assert infralink.__version__ == "0.2.0"


def test_project_uses_dynamic_version() -> None:
    project = load_pyproject()["project"]

    assert project["dynamic"] == ["version"]


def test_bws_optional_dependency_is_supported_sdk_version() -> None:
    optional_dependencies = load_pyproject()["project"]["optional-dependencies"]

    assert optional_dependencies["bws"] == ["bitwarden-sdk>=2.1,<3"]


def test_dev_optional_dependencies_include_release_tooling() -> None:
    dev_dependencies = load_pyproject()["project"]["optional-dependencies"]["dev"]

    assert "build>=1.2" in dev_dependencies
    assert "twine>=5.1" in dev_dependencies
    assert "jsonschema>=4.23" in dev_dependencies


def test_project_urls_are_canonical() -> None:
    project_urls = load_pyproject()["project"]["urls"]

    assert project_urls == {
        "Homepage": "https://github.com/cyberstorm-dev/infralink",
        "Issues": "https://github.com/cyberstorm-dev/infralink/issues",
        "Source": "https://github.com/cyberstorm-dev/infralink",
    }
