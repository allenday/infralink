from pathlib import Path

from scripts.check_docs import check_markdown_links, slugify_heading

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_markdown_links_are_valid() -> None:
    assert check_markdown_links(PROJECT_ROOT) == []


def test_slugify_heading_matches_github_style_basics() -> None:
    assert slugify_heading("Release Adoption And Rollback") == "release-adoption-and-rollback"
    assert slugify_heading("CLI `infralink.cli/v1` Contract") == "cli-infralinkcliv1-contract"
