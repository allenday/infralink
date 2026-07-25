import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "PRD.md",
    PROJECT_ROOT / "BACKLOG.md",
    PROJECT_ROOT / "examples" / "registry.yml",
    PROJECT_ROOT / "examples" / "edges.yml",
    PROJECT_ROOT / "examples" / "roles.yml",
    PROJECT_ROOT / "docs" / "compatibility" / "v0.2.md",
)
FORBIDDEN_NAMES = ("cyberstorm-citadel", "reblogme-app", "relaxgg-ax162", "bdsmlr-db")
TOKEN = re.compile(r"(?<![0-9A-Za-z_.-])[0-9A-Fa-f:.]+(?![0-9A-Za-z_.-])")
URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s`\"'<>]+", re.IGNORECASE)
UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
GCP_KEY = re.compile(r"^\s*(?:gcp_)?project(?:_id)?\s*:\s*(\S+)", re.MULTILINE)
BWS_KEY = re.compile(r"^\s*bws_(?:project|organization)(?:_id)?\s*:\s*(\S+)", re.MULTILINE)


def boundary_violations(text: str) -> list[str]:
    violations: list[str] = []
    lowered = text.lower()
    if ".i.cyberstorm.dev" in lowered:
        violations.append("private suffix")
    violations.extend(f"forbidden name: {name}" for name in FORBIDDEN_NAMES if name in lowered)

    for match in URL.finditer(text):
        parsed = urlsplit(match.group(0).rstrip(".,);"))
        safe_template = parsed.password is not None and parsed.password.startswith("${secret:")
        if (parsed.username is not None or parsed.password is not None) and not safe_template:
            violations.append("credential URL userinfo")

    for token in TOKEN.findall(text):
        candidate = token.strip("[](),;")
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        documentation_ranges = (
            ipaddress.ip_network("192.0.2.0/24"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.0/24"),
        )
        if not (
            isinstance(address, ipaddress.IPv4Address)
            and any(address in network for network in documentation_ranges)
        ):
            violations.append(f"non-RFC5737 address: {address}")

    for project in GCP_KEY.findall(text):
        if "example" not in project.lower():
            violations.append("GCP project identifier")
    for project in BWS_KEY.findall(text):
        if UUID.fullmatch(project):
            violations.append("UUID-shaped BWS project ID")
    return violations


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("host: 10.0.0.7", "non-RFC5737 address"),
        ("host: 2001:db8::1", "non-RFC5737 address"),
        ("host: service.i.cyberstorm.dev", "private suffix"),
        ("gcp_project: production-123", "GCP project identifier"),
        ("bws_project_id: 8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2", "UUID-shaped BWS"),
        ("url: https://user:password@example.com", "credential URL userinfo"),
        ("host: cyberstorm-citadel", "forbidden name"),
    ],
)
def test_boundary_detector_rejects_each_private_data_class(text: str, expected: str) -> None:
    assert any(expected in violation for violation in boundary_violations(text))


def test_boundary_detector_allows_public_examples_and_domain_uuids() -> None:
    text = """
    host: 192.0.2.10
    backup: 198.51.100.5
    endpoint: https://api.example.com
    edge_id: 8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2
    gcp_project: example-project
    """
    assert boundary_violations(text) == []


def test_tracked_public_docs_and_examples_respect_boundary() -> None:
    failures = {
        path.relative_to(PROJECT_ROOT).as_posix(): boundary_violations(
            path.read_text(encoding="utf-8")
        )
        for path in PUBLIC_FILES
        if path.exists() and boundary_violations(path.read_text(encoding="utf-8"))
    }
    assert failures == {}
