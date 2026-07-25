import subprocess
import sys
import venv
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

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
    assert "tomli>=2.0; python_version < '3.11'" in dev_dependencies


def test_project_urls_are_canonical() -> None:
    project_urls = load_pyproject()["project"]["urls"]

    assert project_urls == {
        "Homepage": "https://github.com/cyberstorm-dev/infralink",
        "Issues": "https://github.com/cyberstorm-dev/infralink/issues",
        "Source": "https://github.com/cyberstorm-dev/infralink",
    }


def test_release_wheel_contains_importable_package(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--outdir",
            str(dist_dir),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )

    wheel = next(dist_dir.glob("infralink-*.whl"))
    with zipfile.ZipFile(wheel) as wheel_archive:
        assert "infralink/__init__.py" in wheel_archive.namelist()

    install_venv = tmp_path / "install-venv"
    venv.create(install_venv, with_pip=True)
    install_python = install_venv / "bin" / "python"
    subprocess.run(
        [install_python, "-m", "pip", "install", str(wheel)],
        check=True,
    )
    imported_version = subprocess.run(
        [
            install_python,
            "-c",
            "import infralink; print(infralink.__version__)",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert imported_version.stdout.strip() == "0.2.0"
