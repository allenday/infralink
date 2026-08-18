from pathlib import Path

from scripts.check_docs import check_markdown_links, slugify_heading

from infralink import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CURRENT_VERSION_DOCS = (
    PROJECT_ROOT / "AGENTS.md",
    PROJECT_ROOT / "BACKLOG.md",
    PROJECT_ROOT / "PRD.md",
    PROJECT_ROOT / "README.md",
)


def test_markdown_links_are_valid() -> None:
    assert check_markdown_links(PROJECT_ROOT) == []


def test_current_docs_name_package_version() -> None:
    expected = f"Current package version: `{__version__}`."

    for path in CURRENT_VERSION_DOCS:
        assert expected in path.read_text(encoding="utf-8")


def test_slugify_heading_matches_github_style_basics() -> None:
    assert slugify_heading("Release Adoption And Rollback") == "release-adoption-and-rollback"
    assert slugify_heading("CLI `infralink.cli/v1` Contract") == "cli-infralinkcliv1-contract"
