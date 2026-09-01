"""Policy coverage for PyPI's trusted-publishing projection."""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "publish-pypi.yml"


def load_workflow() -> dict[str, object]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_pypi_workflow_keeps_woodpecker_as_the_release_authority() -> None:
    workflow = load_workflow()

    assert workflow[True] == {
        "workflow_dispatch": {
            "inputs": {
                "tag": {
                    "description": "Existing Infralink release tag to publish to TestPyPI",
                    "required": True,
                    "type": "string",
                }
            }
        },
        "release": {"types": ["published"]},
    }
    assert workflow["permissions"] == {"contents": "read"}


def test_pypi_workflow_uses_trusted_publishing_without_stored_credentials() -> None:
    jobs = load_workflow()["jobs"]
    build_steps = jobs["build"]["steps"]

    assert jobs["publish-testpypi"]["if"] == "github.event_name == 'workflow_dispatch'"
    assert jobs["publish-pypi"]["if"] == "github.event_name == 'release'"
    assert build_steps[0]["with"]["ref"] == (
        "refs/tags/${{ github.event.release.tag_name || inputs.tag }}"
    )
    assert "if" not in build_steps[3]
    assert build_steps[3]["env"] == {
        "RELEASE_TAG": "${{ github.event.release.tag_name || inputs.tag }}"
    }

    for name in ("publish-testpypi", "publish-pypi"):
        job = jobs[name]
        assert job["permissions"] == {"id-token": "write"}
        assert "environment" in job
        assert "from_secret" not in str(job)

    assert jobs["publish-testpypi"]["environment"]["name"] == "testpypi"
    assert jobs["publish-pypi"]["environment"]["name"] == "pypi"
    assert (
        jobs["publish-testpypi"]["steps"][-1]["with"]["repository-url"]
        == "https://test.pypi.org/legacy/"
    )
