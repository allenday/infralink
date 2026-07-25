import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = PROJECT_ROOT / ".github" / "workflows" / "release-candidate.yml"
CI = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
SHA_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def load_workflow(path: Path) -> dict[str, object]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def all_steps(workflow: dict[str, object]) -> list[dict[str, object]]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    return [
        step
        for job in jobs.values()
        if isinstance(job, dict)
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]


def all_run_text(workflow: dict[str, object]) -> str:
    return "\n".join(str(step.get("run", "")) for step in all_steps(workflow))


def test_candidate_is_manual_sha_bound_and_least_privilege() -> None:
    candidate = load_workflow(CANDIDATE)
    triggers = candidate["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    dispatch = triggers["workflow_dispatch"]
    assert dispatch["inputs"]["source_sha"]["required"] == "true"
    assert candidate["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    run_text = all_run_text(candidate)
    candidate_text = CANDIDATE.read_text(encoding="utf-8")
    assert "inputs.source_sha" in candidate_text
    assert "github.sha" in candidate_text
    assert "if: inputs.source_sha != github.sha" in candidate_text
    assert "git rev-parse HEAD" in run_text
    assert "git status --porcelain" in run_text
    assert "persist-credentials: false" in CANDIDATE.read_text(encoding="utf-8")


def test_candidate_builds_once_after_all_nonbuild_gates() -> None:
    candidate = load_workflow(CANDIDATE)
    steps = all_steps(candidate)
    build_steps = [
        index for index, step in enumerate(steps) if "python -m build" in str(step.get("run", ""))
    ]
    assert build_steps == [max(build_steps)]
    run_text = all_run_text(candidate)
    assert run_text.count("python -m build") == 1
    assert "pytest" in run_text
    assert "ruff format --check" in run_text
    assert "ruff check" in run_text
    assert "mypy" in run_text
    assert "generate_cli_schemas.py" in run_text
    assert "gitleaks detect" in run_text
    assert "PIP_NO_INDEX" in run_text
    assert "env -u PYTHONPATH" in run_text
    assert "infralink.__file__" in run_text
    assert "bitwarden_sdk" in run_text
    assert "infralink-secret-canary-47291" in run_text
    assert "tar -xzf dist/infralink-0.2.0.tar.gz" in run_text
    assert "unzip -q dist/infralink-0.2.0-py3-none-any.whl" in run_text
    assert "pytest tests/test_public_data_boundary.py -q --no-cov" in run_text

    pytest_sources = list((PROJECT_ROOT / "tests").glob("test_*.py"))
    offenders = [
        path.name
        for path in pytest_sources
        if path.name != "test_release_workflow_policy.py"
        and re.search(r'["\']-m["\']\s*,\s*["\']build["\']', path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_candidate_attests_and_uploads_only_exact_release_files() -> None:
    candidate = load_workflow(CANDIDATE)
    text = CANDIDATE.read_text(encoding="utf-8")
    attest = [
        step
        for step in all_steps(candidate)
        if str(step.get("uses", "")).startswith("actions/attest-build-provenance@")
    ]
    upload = [
        step
        for step in all_steps(candidate)
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(attest) == 1
    assert len(upload) == 1
    exact_files = (
        "dist/infralink-0.2.0-py3-none-any.whl\n"
        "dist/infralink-0.2.0.tar.gz\n"
        "dist/manifest.json\n"
        "dist/SHA256SUMS"
    )
    assert attest[0]["with"]["subject-path"] == exact_files
    assert upload[0]["with"]["path"] == exact_files
    assert upload[0]["with"]["if-no-files-found"] == "error"
    assert upload[0]["with"]["overwrite"] == "false"
    assert int(upload[0]["with"]["retention-days"]) >= 7
    assert "artifact-id" in text
    assert "artifact-digest" in text
    assert r"^sha256:[0-9a-f]{64}$" in text
    assert "GITHUB_STEP_SUMMARY" in text


def test_all_workflow_actions_are_pinned_and_no_publish_or_deploy_occurs() -> None:
    for path in (CI, CANDIDATE):
        workflow = load_workflow(path)
        for step in all_steps(workflow):
            if "uses" in step:
                assert SHA_ACTION.fullmatch(str(step["uses"])), step["uses"]
        run_text = all_run_text(workflow)
        forbidden = (
            "gh release",
            "twine upload",
            "docker push",
            "git tag",
            "git push",
            "kubectl",
            "ansible-playbook",
        )
        assert not any(command in run_text for command in forbidden)


def test_ci_matrix_and_release_gates_are_complete() -> None:
    load_workflow(CI)
    text = CI.read_text(encoding="utf-8")
    assert all(version in text for version in ('"3.10"', '"3.11"', '"3.12"'))
    assert 'pip install -e ".[dev]"' in text
    assert "|| pip install" not in text
    assert "from infralink.cli.errors import ErrorCode" in text
    assert "twine check" in text
    assert "PIP_NO_INDEX" in text
    assert "env -u PYTHONPATH" in text
    assert "infralink.__file__" in text
    assert "bitwarden_sdk" in text
    assert "infralink version" in text
    assert "gitleaks detect" in text
    assert "tar -xzf dist/infralink-0.2.0.tar.gz" in text
    assert "unzip -q dist/infralink-0.2.0-py3-none-any.whl" in text
    assert "pytest tests/test_public_data_boundary.py -q --no-cov" in text
