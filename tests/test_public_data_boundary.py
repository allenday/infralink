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
    r"(?:\[[0-9a-f:.]+\]?|[0-9a-f:](?:[0-9a-f:.]*[0-9a-f])?)"
    r"""[.,;:!?)}\]>'"]*"""
    r"(?![0-9A-Za-z_.:-])",
    re.IGNORECASE,
)
PORT_AUTHORITY_TOKEN = re.compile(
    r"(?<![a-z0-9_.:@/-])"
    r"(?:\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)"
    r":[a-z0-9-]+"
    r"(?:/[^\s`\"'<>]*)?"
    r"""[.,;:!?)}\]>'"]*""",
    re.IGNORECASE,
)
URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s`\"'<>]+", re.IGNORECASE)
PROTOCOL_RELATIVE_AUTHORITY = re.compile(r"//[^\s`\"'<>]+")
SECRET_TEMPLATE = re.compile(r"\$\{secret:[^}\s]+\}")
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
SAFE_LINE_REFERENCE_FILES = {
    token for token in SAFE_DOTTED_TOKENS if token.endswith((".json", ".md", ".yaml", ".yml"))
}
GCP_PROJECT_ALLOWLIST = {"example-project", "example-staging-project"}
GCP_PROJECT_PLACEHOLDER = "<project-id>"
BWS_PLACEHOLDERS = {
    "organization": {"<organization-id>", "<organization-uuid>"},
    "project": {"<project-id>"},
}
SAFE_HOST_PLACEHOLDERS = {"${host}", "${hostname}", "<host>", "<hostname>"}


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


def _normalize_prose_token(value: str) -> str:
    candidate = value
    while candidate and candidate[-1] in TERMINAL_PROSE_PUNCTUATION:
        candidate = candidate[:-1]
    return candidate


def _parse_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    candidate = value
    while candidate:
        if candidate.startswith("[") != candidate.endswith("]"):
            if candidate[-1] not in TERMINAL_PROSE_PUNCTUATION:
                return None
        literal = (
            candidate[1:-1] if candidate.startswith("[") and candidate.endswith("]") else candidate
        )
        try:
            return ipaddress.ip_address(literal)
        except ValueError:
            if candidate[-1] not in TERMINAL_PROSE_PUNCTUATION:
                return None
            if candidate.startswith("[") and candidate[-1] == "]":
                return None
            candidate = candidate[:-1]
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
        return "invalid endpoint authority"
    address = _parse_address(hostname)
    if address is not None:
        return _address_violation(address)
    candidate = _normalize_prose_token(hostname).lower()
    if UUID.fullmatch(candidate):
        return None
    if not HOSTNAME.fullmatch(candidate) or not any(character.isalpha() for character in candidate):
        return "invalid endpoint authority"
    if candidate == "example.com" or candidate.endswith(".example.com"):
        return None
    return f"non-example hostname: {candidate}"


def _authority_violation(value: str, *, allow_placeholder: bool = False) -> str | None:
    candidate = value.strip("`'\"")
    if allow_placeholder and candidate.lower() in SAFE_HOST_PLACEHOLDERS:
        return None
    normalized_candidate = _normalize_prose_token(candidate)
    filename, separator, line_number = normalized_candidate.rpartition(":")
    if separator and line_number.isdigit() and filename.lower() in SAFE_LINE_REFERENCE_FILES:
        return None
    address = _parse_address(candidate)
    if address is not None:
        return _address_violation(address)

    while candidate:
        authority_text = candidate.split("://", maxsplit=1)[-1].removeprefix("//")
        userinfo, marker, _remainder = authority_text.partition("@")
        safe_template_userinfo = marker and re.fullmatch(
            r"[^:@/]+:\$\{secret:[^}\s]+\}",
            userinfo,
        )
        if marker and safe_template_userinfo is None:
            return "credential URL userinfo"
        sanitized_candidate = SECRET_TEMPLATE.sub("secret-placeholder", candidate)
        authority_input = (
            sanitized_candidate
            if "://" in sanitized_candidate or sanitized_candidate.startswith("//")
            else f"//{sanitized_candidate}"
        )
        try:
            parsed = urlsplit(authority_input)
            hostname = parsed.hostname
            _port = parsed.port
        except ValueError:
            if candidate[-1] in TERMINAL_PROSE_PUNCTUATION:
                candidate = candidate[:-1]
                continue
            return "invalid endpoint authority"
        netloc = parsed.netloc.rsplit("@", maxsplit=1)[-1]
        if hostname is None or netloc.endswith(":"):
            return "invalid endpoint authority"
        return _hostname_violation(hostname)
    return "invalid endpoint authority"


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
        or "_" in candidate
        or VERSION_IDENTIFIER.fullmatch(candidate)
        or _is_safe_dotted_token(text, match, candidate)
    ):
        return None
    return _authority_violation(candidate)


def boundary_violations(text: str) -> list[str]:
    violations: list[str] = []
    seen_findings: set[tuple[int, int, str, str]] = set()
    authority_spans: list[tuple[int, int]] = []

    def record(
        violation: str | None,
        *,
        span: tuple[int, int] = (-1, -1),
        value: str = "",
    ) -> None:
        if violation is None:
            return
        category = violation.split(":", maxsplit=1)[0]
        canonical_value = _normalize_prose_token(value).lower()
        key = (*span, category, canonical_value)
        if key not in seen_findings:
            seen_findings.add(key)
            violations.append(violation)

    def inside_authority_span(span: tuple[int, int]) -> bool:
        return any(start <= span[0] and span[1] <= end for start, end in authority_spans)

    def process_authority(
        value: str,
        span: tuple[int, int],
        *,
        allow_placeholder: bool = False,
    ) -> None:
        if inside_authority_span(span):
            return
        authority_spans.append(span)
        record(
            _authority_violation(value, allow_placeholder=allow_placeholder),
            span=span,
            value=value,
        )

    lowered = text.lower()
    if ".i.cyberstorm.dev" in lowered:
        violations.append("private suffix")
    violations.extend(f"forbidden name: {name}" for name in FORBIDDEN_NAMES if name in lowered)

    url_matches = tuple(URL.finditer(text))
    for match in url_matches:
        process_authority(match.group(0), match.span())

    for match in PROTOCOL_RELATIVE_AUTHORITY.finditer(text):
        process_authority(match.group(0), match.span())

    for match in HOST_FIELD.finditer(text):
        process_authority(match.group(1), match.span(1), allow_placeholder=True)
    for match in HOST_LIST_ITEM.finditer(text):
        process_authority(match.group(1), match.span(1))

    for match in PORT_AUTHORITY_TOKEN.finditer(text):
        process_authority(match.group(0), match.span())

    for match in DOTTED_TOKEN.finditer(text):
        if not inside_authority_span(match.span()):
            record(
                _dotted_token_violation(text, match, url_matches),
                span=match.span(),
                value=match.group(0),
            )

    for match in ADDRESS_TOKEN.finditer(text):
        if inside_authority_span(match.span()):
            continue
        token = match.group(0)
        address = _parse_address(token)
        if address is not None:
            record(_address_violation(address), span=match.span(), value=str(address))
        elif "[" in token or "]" in token:
            record("invalid endpoint authority", span=match.span(), value=token)

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
        ("host: 10.0.0.7:5432", "non-RFC5737 address"),
        ("host: [2001:db8::1]:5432", "non-RFC5737 address"),
        ("endpoint: http://10.0.0.7:8080/path", "non-RFC5737 address"),
        ("endpoint: http://[2001:db8::1]:8080/path", "non-RFC5737 address"),
        ("host: service.i.cyberstorm.dev", "private suffix"),
        ("gcp_project: production-123", "GCP project identifier"),
        ("bws_project_id: 8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2", "UUID-shaped BWS"),
        ("url: https://user:password@example.com", "credential URL userinfo"),
        ("host: //user:password@example.com", "credential URL userinfo"),
        ("endpoint: //user@example.com/path", "credential URL userinfo"),
        ("Connect to //user:password@example.com/path.", "credential URL userinfo"),
        (
            "endpoint: //app:prefix-${secret:example/password}@example.com/path",
            "credential URL userinfo",
        ),
        ("host: cyberstorm-citadel", "forbidden name"),
        ("host: db.corp.invalid", "non-example hostname"),
        ("endpoint: https://api.relax.gg/v1", "non-example hostname"),
        ("canonical_name: private.internal", "non-example hostname"),
        ("host: localhost", "non-example hostname"),
        ("host: privatehost", "non-example hostname"),
        ("host: localhost:5432", "non-example hostname"),
        ("host: privatehost:5432", "non-example hostname"),
        ("endpoint: db.corp.invalid:443/path", "non-example hostname"),
        ("endpoint: //db.corp.invalid:443/path", "non-example hostname"),
        ("endpoint: https://db.corp.invalid:443/path", "non-example hostname"),
        ("Connect to privatehost:5432.", "non-example hostname"),
        ("Connect to db.production.internal:5432/path.", "non-example hostname"),
        ("host: privatehost:notaport", "invalid endpoint authority"),
        ("host: example.com:99999", "invalid endpoint authority"),
        ("endpoint: https://example.com:notaport/path", "invalid endpoint authority"),
        ("Connect to example.com:99999.", "invalid endpoint authority"),
        ("host: [2001:db8::1", "invalid endpoint authority"),
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
        ("Dependency: secrets.production.py:12", "non-example hostname"),
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
    host: 192.0.2.10:5432
    host: <host>
    hostname: ${hostname}
    backup: 198.51.100.5
    prose_address: 203.0.113.9...);!?
    prose_endpoint: 192.0.2.30:5432.
    prose_endpoint: api.example.com:443.
    source: http://192.0.2.20...);!?
    endpoint: https://api.example.com
    endpoint: api.example.com:8443/path
    endpoint: https://api.example.com:443/path
    source: //storage.example.com:443/backup
    endpoint: //app:${secret:example/password}@example.com/path
    source: postgresql://app:${secret:example/password}@192.0.2.40:5432/app
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
    line_refs: manifest.json:12 and registry.yml:12 are public file references.
    path: docs/reference.md is an unambiguous public file path.
    punctuation: docs/reference.md...);!? Release v0.2.0...,"\']
    """
    assert boundary_violations(text) == []


def test_boundary_detector_reports_atomic_authority_findings() -> None:
    assert boundary_violations("host: [2001:db8::1]:5432") == ["non-RFC5737 address: 2001:db8::1"]
    assert boundary_violations("host: [2001:db8::1") == ["invalid endpoint authority"]


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
