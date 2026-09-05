from pathlib import Path

import yaml
from scripts.check_docs import check_markdown_links, slugify_heading

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_markdown_links_are_valid() -> None:
    assert check_markdown_links(PROJECT_ROOT) == []


def test_slugify_heading_matches_github_style_basics() -> None:
    assert slugify_heading("Release Adoption And Rollback") == "release-adoption-and-rollback"
    assert slugify_heading("CLI `infralink.cli/v1` Contract") == "cli-infralinkcliv1-contract"


def test_public_private_runtime_boundary_is_documented() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Start Here" in readme
    assert "[Architecture](docs/architecture.md)" in readme
    assert "[Observable model](docs/observable-model.md)" in readme
    assert "`cyberstorm-dev/infralink-ops`" in readme
    architecture = (PROJECT_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    release = (PROJECT_ROOT / "docs" / "release-operator-workflow.md").read_text(encoding="utf-8")

    for token in [
        "## Public Boundary",
        "cyberstorm-dev/infralink-ops",
        "registry checkout",
        "BWS-backed secret rendering",
        "infralink-host",
        "Do not use the public Infralink package as a deployment controller by itself.",
        "[docs/architecture.md#public-and-private-runtime-boundary]",
    ]:
        assert token in readme

    for token in [
        "## Public And Private Runtime Boundary",
        "This repository is the public package boundary.",
        "Private host-controller packaging belongs to",
        "publish controller images",
        "activate services on a host",
    ]:
        assert token in architecture

    for token in [
        "private host-controller adoption",
        "infralink-ops",
        "It does not publish or select the private controller image.",
    ]:
        assert token in release


def test_cross_repo_authority_map_is_discoverable() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (PROJECT_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    authority_map = (PROJECT_ROOT / "docs" / "control-plane-authority-map.md").read_text(
        encoding="utf-8"
    )

    assert "[Control-plane authority map](docs/control-plane-authority-map.md)" in readme
    assert "[Control-plane authority map](control-plane-authority-map.md)" in architecture
    for token in [
        "# Control-Plane Authority Map",
        "## BLUF",
        "## Authority At A Glance",
        "## Supported Operator Path",
        "## Migration Boundary",
        "`cyberstorm-dev/infralink`",
        "`cyberstorm-dev/infralink-ops`",
        "`relax-dot-gg/infra-management`",
        "`relaxgg/infra-registry`",
        "Registry is the sole desired-state authority",
        "do not copy a runbook into this page",
    ]:
        assert token in authority_map


def test_docs_fast_path_is_documented() -> None:
    architecture = (PROJECT_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    workflow = yaml.safe_load((PROJECT_ROOT / ".woodpecker.yml").read_text(encoding="utf-8"))
    docs_paths = workflow["steps"]["docs-contract"]["when"][0]["path"]["include"]
    quality_paths = workflow["steps"]["quality-3.12"]["when"][0]["path"]["exclude"]

    assert docs_paths == quality_paths
    for token in [
        "## CI Fast Path",
        "`docs-contract`",
        "documentation contract inputs",
        "full Python 3.12 quality gate",
        "generated schemas",
        *(f"`{path}`" for path in docs_paths),
    ]:
        assert token in architecture


def test_safe_cli_workflow_is_discoverable_and_preserves_deployment_boundary() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    workflow = (PROJECT_ROOT / "docs" / "safe-cli-workflow.md").read_text(encoding="utf-8")

    assert "[Safe CLI workflow](docs/safe-cli-workflow.md)" in readme
    for token in [
        "# Safe Infralink CLI Workflow",
        "## BLUF",
        "## Inspect A Declared Topology",
        "## Validate Before Consuming",
        "## Generate A Local Diagram",
        "## Inspect Release Evidence",
        "infralink --registry registry.yml --edges edges.yml info",
        "infralink --registry registry.yml --edges edges.yml validate --strict --check-resolution",
        "infralink --registry /srv/infra-registry",
        "diagram --output ./artifacts --diagram-format all",
        "infralink release inspect",
        "does not select a registry revision, render secrets, or activate services",
    ]:
        assert token in workflow
