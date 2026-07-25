from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import infralink
from infralink.cli.artifacts import (
    artifact_platform_failure,
    artifact_recovery_failure,
    artifact_write_failure,
)
from infralink.cli.errors import ErrorCode

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXIT_CODE_CONTRACT = {
    0: "Positive domain result",
    1: "Completed negative domain result",
    2: "Usage error",
    3: "Input, schema, or entity error",
    4: "Provider or authentication failure",
    69: "Unsupported platform",
    70: "Unexpected internal failure",
    74: "Artifact I/O failure or retained recovery state",
}


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
    assert "tomli>=2.0; python_version < '3.11'" in dev_dependencies


def test_project_urls_are_canonical() -> None:
    project_urls = load_pyproject()["project"]["urls"]

    assert project_urls == {
        "Homepage": "https://github.com/cyberstorm-dev/infralink",
        "Issues": "https://github.com/cyberstorm-dev/infralink/issues",
        "Source": "https://github.com/cyberstorm-dev/infralink",
    }


def test_artifact_commands_publish_their_posix_platform_boundary() -> None:
    project = load_pyproject()["project"]
    classifiers = set(project["classifiers"])

    assert "Operating System :: POSIX :: Linux" in classifiers
    assert "Operating System :: OS Independent" not in classifiers
    assert "Artifact-generating commands require POSIX" in (PROJECT_ROOT / "README.md").read_text(
        encoding="utf-8"
    )


def test_documented_exit_codes_match_artifact_error_behavior() -> None:
    contract_rows = "\n".join(
        f"| `{exit_code}` | {meaning} |"
        for exit_code, meaning in EXIT_CODE_CONTRACT.items()
    )
    documents = (
        "PRD.md",
        "README.md",
        "docs/releases/v0.2.0.md",
        "docs/superpowers/specs/2026-07-25-infralink-v0.2-foundation-design.md",
    )

    for document in documents:
        text = (PROJECT_ROOT / document).read_text(encoding="utf-8")
        assert contract_rows in text, f"{document} has drifted from the exit-code contract"

    platform_failure = artifact_platform_failure()
    assert (platform_failure.code, platform_failure.exit_code) == (
        ErrorCode.UNSUPPORTED_PLATFORM,
        69,
    )
    for failure in (artifact_write_failure(), artifact_recovery_failure()):
        assert (failure.code, failure.exit_code) == (ErrorCode.INTERNAL_ERROR, 74)


def test_release_wheel_includes_the_importable_package() -> None:
    wheel_config = load_pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel_config["packages"] == ["src/infralink"]
    assert wheel_config["force-include"] == {
        "src/infralink/schemas": "infralink/schemas",
    }


def test_release_sdist_has_an_explicit_public_allowlist() -> None:
    sdist_config = load_pyproject()["tool"]["hatch"]["build"]["targets"]["sdist"]

    assert sdist_config["include"] == [
        "src/infralink",
        "README.md",
        "PRD.md",
        "BACKLOG.md",
        "docs/compatibility",
        "examples",
    ]
