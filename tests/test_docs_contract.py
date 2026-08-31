from pathlib import Path

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
