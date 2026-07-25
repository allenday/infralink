import ipaddress
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PUBLIC_FILES = (
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "PRD.md",
    PROJECT_ROOT / "BACKLOG.md",
)
EXPECTED_PUBLIC_FILES = ROOT_PUBLIC_FILES + (
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
HOST_FIELD = re.compile(
    r"^\s*(?:host|hostname|canonical_name|tailscale_name|endpoint|url|source)\s*:\s*(\S+)",
    re.MULTILINE | re.IGNORECASE,
)
HOST_LIST_ITEM = re.compile(
    r"^\s*-\s*((?:[a-z0-9-]+\.)+[a-z]{2,}\.?)(?:\s+#.*)?$",
    re.MULTILINE | re.IGNORECASE,
)
HOSTNAME = re.compile(
    r"^(?=.{1,253}\.?$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$",
    re.IGNORECASE,
)


def tracked_public_files() -> tuple[Path, ...]:
    tracked_names = (
        subprocess.run(
            [
                "git",
                "ls-files",
                "-z",
                "--",
                "README.md",
                "PRD.md",
                "BACKLOG.md",
                "examples",
                "docs/compatibility",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )
    tracked = {PROJECT_ROOT / name for name in tracked_names if name}
    missing_expected = set(EXPECTED_PUBLIC_FILES) - tracked
    if missing_expected:
        raise AssertionError(f"missing tracked public files: {sorted(missing_expected)}")
    for directory in (PROJECT_ROOT / "examples", PROJECT_ROOT / "docs" / "compatibility"):
        if not any(path.is_relative_to(directory) for path in tracked):
            raise AssertionError(f"missing tracked public files under {directory.name}")
    if any(not path.is_file() or path.is_symlink() for path in tracked):
        raise AssertionError("tracked public files must be regular files")
    return tuple(sorted(tracked))


def _hostname_violation(hostname: str | None) -> str | None:
    if hostname is None:
        return None
    candidate = hostname.rstrip(".").lower()
    if UUID.fullmatch(candidate):
        return None
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        return None
    if not HOSTNAME.fullmatch(candidate) or not any(character.isalpha() for character in candidate):
        return None
    if candidate == "example.com" or candidate.endswith(".example.com"):
        return None
    return f"non-example hostname: {candidate}"


def boundary_violations(text: str) -> list[str]:
    violations: list[str] = []
    lowered = text.lower()
    if ".i.cyberstorm.dev" in lowered:
        violations.append("private suffix")
    violations.extend(f"forbidden name: {name}" for name in FORBIDDEN_NAMES if name in lowered)

    for match in URL.finditer(text):
        raw_url = match.group(0).rstrip(".,);")
        safe_template = re.search(r":\$\{secret:[^}\s]+\}@", raw_url) is not None
        parsed = urlsplit(re.sub(r"\$\{secret:[^}\s]+\}", "placeholder", raw_url))
        if (parsed.username is not None or parsed.password is not None) and not safe_template:
            violations.append("credential URL userinfo")
        hostname_violation = _hostname_violation(parsed.hostname)
        if hostname_violation is not None:
            violations.append(hostname_violation)

    for value in HOST_FIELD.findall(text):
        candidate = value.strip("'\",[]()")
        if "://" in candidate:
            continue
        if candidate.startswith("//"):
            candidate = urlsplit(f"placeholder:{candidate}").hostname or candidate
        else:
            candidate = candidate.split("/", maxsplit=1)[0]
        hostname_violation = _hostname_violation(candidate)
        if hostname_violation is not None:
            violations.append(hostname_violation)
    for candidate in HOST_LIST_ITEM.findall(text):
        hostname_violation = _hostname_violation(candidate)
        if hostname_violation is not None:
            violations.append(hostname_violation)

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
        ("host: db.corp.invalid", "non-example hostname"),
        ("endpoint: https://api.relax.gg/v1", "non-example hostname"),
        ("canonical_name: private.internal", "non-example hostname"),
        ("host: localhost", "non-example hostname"),
    ],
)
def test_boundary_detector_rejects_each_private_data_class(text: str, expected: str) -> None:
    assert any(expected in violation for violation in boundary_violations(text))


def test_boundary_detector_allows_public_examples_and_domain_uuids() -> None:
    text = """
    host: 192.0.2.10
    backup: 198.51.100.5
    endpoint: https://api.example.com
    canonical_name: db.internal.example.com
    edge_id: 8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2
    gcp_project: example-project
    prose: infralink.cli/v1, manifest.json, and resolver.get are identifiers.
    """
    assert boundary_violations(text) == []


def test_tracked_public_docs_and_examples_respect_boundary() -> None:
    failures = {
        path.relative_to(PROJECT_ROOT).as_posix(): boundary_violations(
            path.read_text(encoding="utf-8")
        )
        for path in tracked_public_files()
        if path.exists() and boundary_violations(path.read_text(encoding="utf-8"))
    }
    assert failures == {}


def test_public_file_inventory_tracks_all_shipped_examples_and_compatibility_docs() -> None:
    tracked = {
        PROJECT_ROOT / path
        for path in subprocess.run(
            ["git", "ls-files"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    }
    expected = set(ROOT_PUBLIC_FILES) | {
        path
        for path in tracked
        if path.is_relative_to(PROJECT_ROOT / "examples")
        or path.is_relative_to(PROJECT_ROOT / "docs" / "compatibility")
    }

    assert set(tracked_public_files()) == expected
