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
ADDRESS_TOKEN = re.compile(
    r"(?<![0-9A-Za-z_.:-])"
    r"[0-9a-f:](?:[0-9a-f:.]*[0-9a-f])?"
    r"""[.,;:!?)}\]>'"]*"""
    r"(?![0-9A-Za-z_.:-])",
    re.IGNORECASE,
)
URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s`\"'<>]+", re.IGNORECASE)
UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
GCP_KEY = re.compile(
    r"(?<![a-z0-9_])(?:export\s+)?"
    r"(?:(?:gcp_)?project(?:_id)?|google_cloud_project)[ \t]*[:=][ \t]*"
    r"[`'\"]?(?P<value>[^\s`'\"]*)",
    re.IGNORECASE,
)
BWS_KEY = re.compile(
    r"(?<![a-z0-9_])(?:export\s+)?"
    r"bws_(?P<kind>project|organization)(?:_id)?[ \t]*[:=][ \t]*"
    r"[`'\"]?(?P<value>[^\s`'\"]*)",
    re.IGNORECASE,
)
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
DOTTED_TOKEN = re.compile(
    r"(?<![a-z0-9_.-])"
    r"(?=[a-z0-9_.-]*\.[a-z0-9])"
    r"[a-z0-9](?:[a-z0-9_.-]*[a-z0-9])?"
    r"""[.,;:!?)}\]>'"]*"""
    r"(?![a-z0-9_.-])",
    re.IGNORECASE,
)
VERSION_IDENTIFIER = re.compile(r"^v?\d+(?:\.\d+){1,3}$", re.IGNORECASE)
TERMINAL_PROSE_PUNCTUATION = ".,;:!?)]}>'\""
DOCUMENTATION_IPV4_RANGES = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
SAFE_DOTTED_TOKENS = {
    "app-database.md",
    "backlog.md",
    "edges.yml",
    "edgeset.load",
    "infralink-0.2.0-py3-none-any.whl",
    "infralink-0.2.0.tar.gz",
    "infralink.cli",
    "manifest.json",
    "prd.md",
    "registry.load",
    "registry.yml",
    "resolver.get",
    "roles.yml",
    "v0.2.md",
}
GCP_PROJECT_ALLOWLIST = {"example-project", "example-staging-project"}
GCP_PROJECT_PLACEHOLDER = "<project-id>"
BWS_PLACEHOLDERS = {
    "organization": {"<organization-id>", "<organization-uuid>"},
    "project": {"<project-id>"},
}


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


def _normalize_prose_token(value: str, *, preserve_valid_address: bool = False) -> str:
    candidate = value
    while candidate and candidate[-1] in TERMINAL_PROSE_PUNCTUATION:
        if preserve_valid_address:
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                pass
            else:
                return candidate
        candidate = candidate[:-1]
    return candidate


def _parse_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(_normalize_prose_token(value, preserve_valid_address=True))
    except ValueError:
        return None


def _address_violation(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str | None:
    if isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in DOCUMENTATION_IPV4_RANGES
    ):
        return None
    return f"non-RFC5737 address: {address}"


def _hostname_violation(hostname: str | None) -> str | None:
    if hostname is None:
        return None
    candidate = _normalize_prose_token(hostname).lower()
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


def _is_safe_dotted_token(text: str, match: re.Match[str], candidate: str) -> bool:
    candidate = candidate.lower()
    if candidate in SAFE_DOTTED_TOKENS:
        return True
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = re.split(r"[\s`'\"(<\[]", text[line_start : match.start()])[-1]
    return prefix.startswith(("docs/", "examples/")) and candidate.rsplit(".", maxsplit=1)[-1] in {
        "json",
        "md",
        "yaml",
        "yml",
    }


def _dotted_token_violation(
    text: str,
    match: re.Match[str],
    url_matches: tuple[re.Match[str], ...],
) -> str | None:
    candidate = _normalize_prose_token(match.group(0))
    if _parse_address(candidate) is not None:
        return None
    inside_url = any(
        url_match.start() <= match.start() and match.end() <= url_match.end()
        for url_match in url_matches
    )
    if (
        inside_url
        or VERSION_IDENTIFIER.fullmatch(candidate)
        or _is_safe_dotted_token(text, match, candidate)
    ):
        return None
    return _hostname_violation(candidate)


def boundary_violations(text: str) -> list[str]:
    violations: list[str] = []
    lowered = text.lower()
    if ".i.cyberstorm.dev" in lowered:
        violations.append("private suffix")
    violations.extend(f"forbidden name: {name}" for name in FORBIDDEN_NAMES if name in lowered)

    url_matches = tuple(URL.finditer(text))
    for match in url_matches:
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

    for match in DOTTED_TOKEN.finditer(text):
        hostname_violation = _dotted_token_violation(text, match, url_matches)
        if hostname_violation is not None:
            violations.append(hostname_violation)

    for token in ADDRESS_TOKEN.findall(text):
        address = _parse_address(token)
        if address is None:
            continue
        address_violation = _address_violation(address)
        if address_violation is not None:
            violations.append(address_violation)

    for match in GCP_KEY.finditer(text):
        project = match.group("value")
        if project.lower() not in GCP_PROJECT_ALLOWLIST | {GCP_PROJECT_PLACEHOLDER}:
            violations.append("GCP project identifier")
    for match in BWS_KEY.finditer(text):
        kind = match.group("kind").lower()
        value = match.group("value").lower()
        if value in BWS_PLACEHOLDERS[kind]:
            continue
        if not value or UUID.fullmatch(value.strip("<>")) or "<" in value or ">" in value:
            violations.append(f"BWS {kind} identifier (UUID-shaped BWS project ID)")
    return violations


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("host: 10.0.0.7", "non-RFC5737 address"),
        ("host: 2001:db8::1", "non-RFC5737 address"),
        ("Address: 10.0.0.7.", "non-RFC5737 address"),
        ("Address: 10.0.0.7..", "non-RFC5737 address"),
        ("Address: 10.0.0.7...", "non-RFC5737 address"),
        ("Address: 10.0.0.7...);!?", "non-RFC5737 address"),
        ("Address: 2001:db8::1.", "non-RFC5737 address"),
        ("Address: 2001:db8::1...", "non-RFC5737 address"),
        ("Address: 2001:db8::1...,\"']", "non-RFC5737 address"),
        ("Address: 2001:db8::", "non-RFC5737 address"),
        ("Address: 2001:db8::...);", "non-RFC5737 address"),
        ("host: 10.0.0.7...", "non-RFC5737 address"),
        ("host: 2001:db8::1...);", "non-RFC5737 address"),
        ("endpoint: http://10.0.0.7.", "non-RFC5737 address"),
        ("endpoint: http://10.0.0.7...", "non-RFC5737 address"),
        ("endpoint: http://10.0.0.7...);!?", "non-RFC5737 address"),
        ("endpoint: http://[2001:db8::1].", "non-RFC5737 address"),
        ("host: service.i.cyberstorm.dev", "private suffix"),
        ("gcp_project: production-123", "GCP project identifier"),
        ("bws_project_id: 8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2", "UUID-shaped BWS"),
        ("url: https://user:password@example.com", "credential URL userinfo"),
        ("host: cyberstorm-citadel", "forbidden name"),
        ("host: db.corp.invalid", "non-example hostname"),
        ("endpoint: https://api.relax.gg/v1", "non-example hostname"),
        ("canonical_name: private.internal", "non-example hostname"),
        ("host: localhost", "non-example hostname"),
        ("Dependency: db.production.internal", "non-example hostname"),
        ("See [database](db.production.internal).", "non-example hostname"),
        (
            "BWS_PROJECT_ID=8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2",
            "UUID-shaped BWS",
        ),
        ("GOOGLE_CLOUD_PROJECT=production-123", "GCP project identifier"),
        ("gcp_project: production-example-real", "GCP project identifier"),
        (
            "export BWS_ORGANIZATION_ID=8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2",
            "UUID-shaped BWS",
        ),
        (
            "Set `BWS_PROJECT_ID: 8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2` here.",
            "UUID-shaped BWS",
        ),
        ("Run `export GOOGLE_CLOUD_PROJECT=production-123`.", "GCP project identifier"),
        ("Use GCP_PROJECT_ID: production-example-real.", "GCP project identifier"),
        ("export GOOGLE_CLOUD_PROJECT=<production-123>", "GCP project identifier"),
        ("Dependency: secrets.production.py", "non-example hostname"),
        ("See secrets.production.md for credentials.", "non-example hostname"),
        ("Dependency: db.production.internal.", "non-example hostname"),
        ("Dependency: db.production.internal..", "non-example hostname"),
        ("Dependency: db.production.internal...", "non-example hostname"),
        ("Dependency: secrets.production.py.", "non-example hostname"),
        ("See secrets.production.md.", "non-example hostname"),
        ("See secrets.production.py...);!?", "non-example hostname"),
        ("See secrets.production.md...,\"']", "non-example hostname"),
        ("export GOOGLE_CLOUD_PROJECT=<production-123", "GCP project identifier"),
        ("export GOOGLE_CLOUD_PROJECT=production-123>", "GCP project identifier"),
        ("export GOOGLE_CLOUD_PROJECT=<>", "GCP project identifier"),
        ("export GOOGLE_CLOUD_PROJECT=", "GCP project identifier"),
        ("export GOOGLE_CLOUD_PROJECT=<project-id", "GCP project identifier"),
        ("export GOOGLE_CLOUD_PROJECT=project-id>", "GCP project identifier"),
        (
            "BWS_PROJECT_ID=<8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2",
            "BWS project identifier",
        ),
        (
            "BWS_ORGANIZATION_ID=8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2>",
            "BWS organization identifier",
        ),
        ("BWS_PROJECT_ID=<organization-id>", "BWS project identifier"),
        ("BWS_PROJECT_ID=", "BWS project identifier"),
    ],
)
def test_boundary_detector_rejects_each_private_data_class(text: str, expected: str) -> None:
    assert any(expected in violation for violation in boundary_violations(text))


def test_boundary_detector_allows_public_examples_and_domain_uuids() -> None:
    text = """
    host: 192.0.2.10
    backup: 198.51.100.5
    prose_address: 203.0.113.9...);!?
    source: http://192.0.2.20...);!?
    endpoint: https://api.example.com
    source: https://example.com/releases/package.whl?download=1
    canonical_name: db.internal.example.com
    edge_id: 8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2
    gcp_project: example-project
    GOOGLE_CLOUD_PROJECT=example-staging-project
    export GOOGLE_CLOUD_PROJECT=<project-id>
    BWS_PROJECT_ID=<project-id>
    BWS_ORGANIZATION_ID=<organization-id>
    export BWS_ORGANIZATION_ID="<organization-uuid>"
    prose: infralink.cli/v1, manifest.json, and resolver.get are identifiers.
    files: registry.yml, edges.yml, PRD.md, and BACKLOG.md are public files.
    path: docs/reference.md is an unambiguous public file path.
    punctuation: docs/reference.md...);!? Release v0.2.0...,"\']
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


def test_compatibility_inventory_uses_anchored_runtime_counts() -> None:
    compatibility = (PROJECT_ROOT / "docs" / "compatibility" / "v0.2.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(compatibility.split())

    assert "63 anchored `from infralink` imports" in normalized
    assert "0 anchored `import infralink` module imports" in normalized
    assert "four loader-module imports and one prose comment" in normalized
    assert "1 PostgreSQL, 1 Redis, and 5 generic URL-helper call-shaped occurrences" in normalized
    assert "generic total includes two documentation examples" in normalized
