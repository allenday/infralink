import ipaddress
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

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
SSH_FINGERPRINT = re.compile(r"(?<![A-Za-z0-9+/])SHA256:[A-Za-z0-9+/]{43}(?![A-Za-z0-9+/])")
PERCENT_ESCAPE = re.compile(r"%[0-9a-f]{2}", re.IGNORECASE)
BRACKET_AUTHORITY_TOKEN = re.compile(r"\[[^\]\s`\"'<>]*\]|\[[^\s`\"'<>]+")
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
    "infralink.release-attestation.v1",
    "infralink.release-attestation.v2",
    "infralink.publisher-request.v2",
    "infralink.publisher-request.v3",
    "infralink.release-candidate.v1",
    "agent-cli.response.v1",
    "infralink.observation",
    "nginx.access",
    "ci.builds",
    "manifest.json",
    "prd.md",
    "registry.load",
    "registry.yml",
    "resolver.get",
    "roles.yml",
    "sha256sums.sigstore.json",
    "v0.2.md",
}
SAFE_LINE_REFERENCE_FILES = {
    token for token in SAFE_DOTTED_TOKENS if token.endswith((".json", ".md", ".yaml", ".yml"))
} | {path.name.lower() for path in ROOT_PUBLIC_FILES}
GCP_PROJECT_ALLOWLIST = {"example-project", "example-staging-project"}
GCP_PROJECT_PLACEHOLDER = "<project-id>"
BWS_PLACEHOLDERS = {
    "organization": {"<organization-id>", "<organization-uuid>"},
    "project": {"<project-id>"},
}
SAFE_HOST_PLACEHOLDERS = {"${host}", "${hostname}", "<host>", "<hostname>"}
PATH_FILE_SUFFIXES = (".json", ".md", ".tar.gz", ".whl", ".yaml", ".yml")
MAX_URL_PAYLOAD_LENGTH = 16_384
MAX_URL_DECODE_ROUNDS = 4
MAX_NESTED_AUTHORITY_DEPTH = 4


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


def _looks_like_ipv6_literal(value: str) -> bool:
    first_group, marker, _remainder = value.partition(":")
    if not marker:
        return False
    return (
        not first_group
        or first_group.isdigit()
        or (value.count(":") >= 2 and re.fullmatch(r"[0-9a-f]{1,4}", first_group, re.IGNORECASE))
    )


def _looks_like_bracketed_endpoint(text: str, start: int, value: str) -> bool:
    hostname, marker, _port_text = value.rpartition(":")
    if not marker or not hostname:
        return False
    hostname = hostname.lower()
    if (
        "." in hostname
        or hostname in {"host", "hostname", "localhost", "privatehost"}
        or re.search(r"(?:^|[-_])host(?:name)?$", hostname) is not None
    ):
        return True
    prefix = text[max(0, start - 32) : start]
    return (
        re.search(
            r"(?:(?:the\s+)?(?:host|endpoint)(?:\s+is)?|"
            r"connect(?:\s+securely)?(?:\s+to)?)\s*[:=]?\s*$",
            prefix,
            re.IGNORECASE,
        )
        is not None
    )


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


def _authority_bounds(value: str) -> tuple[int, int]:
    if value.startswith("//"):
        start = 2
    elif "://" in value:
        start = value.index("://") + 3
    else:
        start = 0
    secret_spans = tuple(match.span() for match in SECRET_TEMPLATE.finditer(value))
    for position in range(start, len(value)):
        if value[position] in "/?#" and not any(
            secret_start <= position < secret_end for secret_start, secret_end in secret_spans
        ):
            return start, position
    return start, len(value)


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
        authority_start, authority_end = _authority_bounds(candidate)
        authority_text = candidate[authority_start:authority_end]
        userinfo, marker, _remainder = authority_text.rpartition("@")
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
            port = parsed.port
        except ValueError:
            if candidate[-1] in TERMINAL_PROSE_PUNCTUATION:
                candidate = candidate[:-1]
                continue
            return "invalid endpoint authority"
        netloc = parsed.netloc.rsplit("@", maxsplit=1)[-1]
        if hostname is None or netloc.endswith(":") or port == 0:
            return "invalid endpoint authority"
        return _hostname_violation(hostname)
    return "invalid endpoint authority"


def _is_safe_dotted_token(
    text: str,
    match: re.Match[str],
    candidate: str,
    *,
    inside_url_path: bool = False,
) -> bool:
    candidate = candidate.lower()
    if inside_url_path and candidate.endswith(PATH_FILE_SUFFIXES):
        return True
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
    *,
    inside_url_payload: bool = False,
    inside_url_path: bool = False,
) -> str | None:
    candidate = _normalize_prose_token(match.group(0))
    if _parse_address(candidate) is not None:
        return None
    percent_escape = text[match.start() - 1 : match.start() + 2]
    if inside_url_payload and match.start() > 0 and PERCENT_ESCAPE.fullmatch(percent_escape):
        return None
    if (
        "_" in candidate
        or VERSION_IDENTIFIER.fullmatch(candidate)
        or _is_safe_dotted_token(
            text,
            match,
            candidate,
            inside_url_path=inside_url_path,
        )
    ):
        return None
    return _authority_violation(candidate)


def _canonical_finding_value(value: str) -> str:
    candidate = value
    for _round in range(MAX_URL_DECODE_ROUNDS):
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return _normalize_prose_token(candidate.strip()).lower()


def boundary_violations(
    text: str,
    *,
    _scan_url_like_authorities: bool = True,
    _url_payload: bool = False,
    _url_path_payload: bool = False,
    _semantic_source: tuple[int, int] | None = None,
    _violations: list[str] | None = None,
    _seen_findings: set[tuple[int, int, str, str]] | None = None,
    _nested_authority_depth: int = 0,
) -> list[str]:
    violations = [] if _violations is None else _violations
    seen_findings = set() if _seen_findings is None else _seen_findings
    authority_spans: list[tuple[int, int]] = []
    ssh_fingerprint_spans = [match.span() for match in SSH_FINGERPRINT.finditer(text)]
    bracket_spans: list[tuple[int, int]] = []
    balanced_bracket_spans: list[tuple[int, int]] = []
    generic_bracket_spans: list[tuple[int, int]] = []
    url_source_spans: list[tuple[int, int]] = []
    url_path_spans: list[tuple[int, int]] = []
    raw_payloads: list[tuple[tuple[int, int], str, bool]] = []
    decoded_payloads: list[tuple[tuple[int, int], str, bool]] = []

    def record(
        violation: str | None,
        *,
        span: tuple[int, int] = (-1, -1),
        value: str = "",
    ) -> None:
        if violation is None:
            return
        source_span = _semantic_source or next(
            (
                source
                for source in url_source_spans
                if source[0] <= span[0] and span[1] <= source[1]
            ),
            span,
        )
        category, separator, detail = violation.partition(":")
        canonical_value = _canonical_finding_value(detail if separator else value)
        key = (*source_span, category.lower(), canonical_value)
        if key not in seen_findings:
            seen_findings.add(key)
            violations.append(violation)

    def inside_authority_span(span: tuple[int, int]) -> bool:
        return any(start <= span[0] and span[1] <= end for start, end in authority_spans)

    def starts_in_authority_span(span: tuple[int, int]) -> bool:
        return any(start <= span[0] < end for start, end in authority_spans)

    def inside_ssh_fingerprint(span: tuple[int, int]) -> bool:
        return any(start <= span[0] < end for start, end in ssh_fingerprint_spans)

    def inside_bracket_span(span: tuple[int, int]) -> bool:
        return any(start <= span[0] < end for start, end in bracket_spans)

    def inside_balanced_bracket_span(span: tuple[int, int]) -> bool:
        return any(start <= span[0] and span[1] <= end for start, end in balanced_bracket_spans)

    def inside_generic_bracket_span(span: tuple[int, int]) -> bool:
        return any(start <= span[0] < end for start, end in generic_bracket_spans)

    def inside_url_path(span: tuple[int, int]) -> bool:
        return _url_path_payload or any(
            start <= span[0] and span[1] <= end for start, end in url_path_spans
        )

    def inside_url_payload(span: tuple[int, int]) -> bool:
        return _url_payload or any(
            start <= span[0] and span[1] <= end for start, end in url_source_spans
        )

    def queue_decoded_payload(
        payload: str,
        source_span: tuple[int, int],
        *,
        path_payload: bool,
    ) -> None:
        if len(payload) > MAX_URL_PAYLOAD_LENGTH:
            record("URL payload exceeds scan limit", span=source_span)
            return
        raw_payloads.append((source_span, payload, path_payload))
        normalized = payload
        for _round in range(MAX_URL_DECODE_ROUNDS):
            decoded = unquote(normalized)
            if decoded == normalized:
                return
            decoded_payloads.append((source_span, decoded, path_payload))
            normalized = decoded
        if PERCENT_ESCAPE.search(normalized):
            record("excessive URL payload encoding", span=source_span)

    def process_authority(
        value: str,
        span: tuple[int, int],
        *,
        allow_placeholder: bool = False,
    ) -> None:
        left_trimmed = len(value) - len(value.lstrip("`'\""))
        candidate = value.strip("`'\"")
        relative_start, relative_end = _authority_bounds(candidate)
        authority_span = (
            span[0] + left_trimmed + relative_start,
            span[0] + left_trimmed + relative_end,
        )
        if inside_authority_span(authority_span):
            return
        url_source_spans.append(span)
        authority_spans.append(authority_span)
        record(
            _authority_violation(value, allow_placeholder=allow_placeholder),
            span=authority_span,
            value=candidate[relative_start:relative_end],
        )
        sanitized_candidate = SECRET_TEMPLATE.sub("secret-placeholder", candidate)
        authority_input = (
            sanitized_candidate
            if "://" in sanitized_candidate or sanitized_candidate.startswith("//")
            else f"//{sanitized_candidate}"
        )
        try:
            parsed = urlsplit(authority_input)
        except ValueError:
            return
        path_end = min(
            (
                position
                for delimiter in "?#"
                if (position := candidate.find(delimiter, relative_end)) >= 0
            ),
            default=len(candidate),
        )
        if parsed.path:
            url_path_spans.append(
                (
                    span[0] + left_trimmed + relative_end,
                    span[0] + left_trimmed + path_end,
                )
            )
            queue_decoded_payload(parsed.path, span, path_payload=True)
        for payload in (parsed.query, parsed.fragment):
            if payload:
                queue_decoded_payload(payload, span, path_payload=False)

    lowered = text.lower()
    if ".i.cyberstorm.dev" in lowered:
        record("private suffix", value=".i.cyberstorm.dev")
    for name in FORBIDDEN_NAMES:
        if name in lowered:
            record(f"forbidden name: {name}", value=name)

    url_matches = tuple(URL.finditer(text)) if _scan_url_like_authorities else ()
    for match in url_matches:
        process_authority(match.group(0), match.span())

    if _scan_url_like_authorities:
        for match in PROTOCOL_RELATIVE_AUTHORITY.finditer(text):
            process_authority(match.group(0), match.span())

    for match in HOST_FIELD.finditer(text):
        process_authority(match.group(1), match.span(1), allow_placeholder=True)
    for match in HOST_LIST_ITEM.finditer(text):
        process_authority(match.group(1), match.span(1))

    for match in BRACKET_AUTHORITY_TOKEN.finditer(text):
        raw_token = match.group(0)
        token = _normalize_prose_token(raw_token)
        closing_bracket = raw_token.find("]")
        bracket_literal = raw_token[1:closing_bracket]
        if (
            closing_bracket > 0
            and ":" in bracket_literal
            and _looks_like_ipv6_literal(bracket_literal)
            and not starts_in_authority_span(match.span())
        ):
            if _parse_address(f"[{bracket_literal}]") is None:
                bracket_spans.append(match.span())
                record("invalid endpoint authority", span=match.span(), value=token)
            else:
                balanced_bracket_spans.append(match.span())
        elif (
            closing_bracket > 0
            and ":" in bracket_literal
            and _looks_like_bracketed_endpoint(text, match.start(), bracket_literal)
        ):
            bracket_spans.append(match.span())
            record(
                _authority_violation(bracket_literal),
                span=match.span(),
                value=bracket_literal,
            )
        elif closing_bracket > 0 and ":" in bracket_literal:
            generic_bracket_spans.append(match.span())
        if "]" not in raw_token and ":" in token and not starts_in_authority_span(match.span()):
            bracket_spans.append(match.span())
            record("invalid endpoint authority", span=match.span(), value=token)

    for match in PORT_AUTHORITY_TOKEN.finditer(text):
        if (
            not inside_ssh_fingerprint(match.span())
            and not inside_bracket_span(match.span())
            and not inside_balanced_bracket_span(match.span())
            and not inside_generic_bracket_span(match.span())
        ):
            process_authority(match.group(0), match.span())

    for match in DOTTED_TOKEN.finditer(text):
        if (
            not inside_authority_span(match.span())
            and not inside_bracket_span(match.span())
            and not inside_balanced_bracket_span(match.span())
        ):
            record(
                _dotted_token_violation(
                    text,
                    match,
                    inside_url_payload=inside_url_payload(match.span()),
                    inside_url_path=inside_url_path(match.span()),
                ),
                span=match.span(),
                value=match.group(0),
            )

    for match in ADDRESS_TOKEN.finditer(text):
        if inside_authority_span(match.span()) or inside_bracket_span(match.span()):
            continue
        token = match.group(0)
        address = _parse_address(token)
        if address is not None:
            record(_address_violation(address), span=match.span(), value=str(address))
        elif "[" in token or "]" in token:
            record("invalid endpoint authority", span=match.span(), value=token)

    def scan_nested_payload(
        source_span: tuple[int, int],
        payload: str,
        path_payload: bool,
        *,
        authority_only: bool,
    ) -> None:
        has_authority = (
            URL.search(payload) is not None
            or PROTOCOL_RELATIVE_AUTHORITY.search(payload) is not None
        )
        if authority_only and not has_authority:
            return
        if has_authority and _nested_authority_depth >= MAX_NESTED_AUTHORITY_DEPTH:
            record("URL authority nesting exceeds scan limit", span=source_span)
            return
        boundary_violations(
            payload,
            _scan_url_like_authorities=True,
            _url_payload=True,
            _url_path_payload=path_payload,
            _semantic_source=source_span,
            _violations=violations,
            _seen_findings=seen_findings,
            _nested_authority_depth=_nested_authority_depth + 1,
        )

    for source_span, raw_payload, path_payload in raw_payloads:
        scan_nested_payload(source_span, raw_payload, path_payload, authority_only=True)
    for source_span, decoded_payload, path_payload in decoded_payloads:
        scan_nested_payload(source_span, decoded_payload, path_payload, authority_only=False)

    for match in GCP_KEY.finditer(text):
        project = match.group("value")
        if project.lower() not in GCP_PROJECT_ALLOWLIST | {GCP_PROJECT_PLACEHOLDER}:
            record("GCP project identifier", span=match.span(), value=project)
    for match in BWS_KEY.finditer(text):
        kind = match.group("kind").lower()
        value = match.group("value").lower()
        if value in BWS_PLACEHOLDERS[kind]:
            continue
        if not value or UUID.fullmatch(value.strip("<>")) or "<" in value or ">" in value:
            record(
                f"BWS {kind} identifier (UUID-shaped BWS project ID)",
                span=match.span(),
                value=value,
            )
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
        ("endpoint: https://example.com/path/db.production.internal", "non-example hostname"),
        ("endpoint: //example.com/path/db.production.internal", "non-example hostname"),
        ("endpoint: example.com/path/db.production.internal", "non-example hostname"),
        ("endpoint: https://example.com/path/db%2Eproduction%2Einternal", "non-example hostname"),
        (
            "endpoint: https://example.com/path/db%252Eproduction%252Einternal",
            "non-example hostname",
        ),
        (
            "endpoint: https://example.com/path/db%25252Eproduction%25252Einternal",
            "non-example hostname",
        ),
        ("endpoint: https://example.com/?target=10.0.0.7", "non-RFC5737 address"),
        ("endpoint: https://example.com/path/10%2E0%2E0%2E7", "non-RFC5737 address"),
        ("endpoint: https://example.com/path/10%252E0%252E0%252E7", "non-RFC5737 address"),
        (
            "endpoint: https://example.com/path/10%25252E0%25252E0%25252E7",
            "non-RFC5737 address",
        ),
        (
            "endpoint: https://example.com/?setting=GCP_PROJECT_ID%3Dproduction-123",
            "GCP project identifier",
        ),
        (
            "endpoint: https://example.com/path/db%252525252Eproduction.internal",
            "excessive URL payload encoding",
        ),
        ("Dependency: dependency.whl", "non-example hostname"),
        ("Dependency: user%40private.internal", "non-example hostname"),
        ("host: dependency.whl", "non-example hostname"),
        ("endpoint: https://example.com/?target=dependency.whl", "non-example hostname"),
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
    source: https://example.com/releases/dependency.whl
    source: https://example.com/archive/192.0.2.50
    source: https://example.com/archive/192%2E0%2E2%2E51
    source: https://example.com/users/user@example.com
    source: https://example.com/users/user%40example.com
    source: https://example.com/users/user%2540example.com
    source: https://example.com/users/api%2Eexample.com
    source: https://example.com/?email=user@example.com
    source: https://example.com/#contact=user@example.com
    canonical_name: db.internal.example.com
    edge_id: 8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2
    gcp_project: example-project
    GOOGLE_CLOUD_PROJECT=example-staging-project
    export GOOGLE_CLOUD_PROJECT=<project-id>
    BWS_PROJECT_ID=<project-id>
    BWS_ORGANIZATION_ID=<organization-id>
    export BWS_ORGANIZATION_ID="<organization-uuid>"
    prose: infralink.cli/v1, infralink.release-candidate.v1,
    infralink.release-attestation.v1, manifest.json, and resolver.get are identifiers.
    files: registry.yml, edges.yml, PRD.md, and BACKLOG.md are public files.
    line_refs: README.md:12, manifest.json:12, and registry.yml:12 are public file references.
    path: docs/reference.md is an unambiguous public file path.
    punctuation: docs/reference.md...);!? Release v0.2.0...,"\']
    """
    assert boundary_violations(text) == []


def test_boundary_detector_allows_canonical_public_ssh_fingerprints() -> None:
    assert boundary_violations(
        "fingerprint: SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    ) == []


def test_boundary_detector_reports_atomic_authority_findings() -> None:
    assert boundary_violations("Use [key:value] in generic prose.") == []
    assert boundary_violations("Use [key:123] [year:2026] [line:12] [HTTP:200].") == []
    assert (
        boundary_violations("Use [ghost:value] [ghost:123] [hostile:notaport] [cohost:5432].") == []
    )
    assert boundary_violations("Use [host:notaport] [db-host:notaport].") == [
        "invalid endpoint authority",
        "invalid endpoint authority",
    ]
    assert boundary_violations("Use [key:value][privatehost:5432] in prose.") == [
        "non-example hostname: privatehost"
    ]
    assert boundary_violations("Use [privatehost:notaport] in prose.") == [
        "invalid endpoint authority"
    ]
    assert boundary_violations("Use [privatehost:0] in prose.") == ["invalid endpoint authority"]
    assert boundary_violations("Use [privatehost:65536] in prose.") == [
        "invalid endpoint authority"
    ]
    assert boundary_violations("The endpoint is [service:5432].") == [
        "non-example hostname: service"
    ]
    assert boundary_violations("The host is [service:notaport].") == ["invalid endpoint authority"]
    assert boundary_violations("Connect securely to [service:5432].") == [
        "non-example hostname: service"
    ]
    assert boundary_violations("Use [key:value], [privatehost:5432] in prose.") == [
        "non-example hostname: privatehost"
    ]
    assert boundary_violations("Use [key:value][2001:db8::gg] in prose.") == [
        "invalid endpoint authority"
    ]
    assert boundary_violations("Address: [2001:db8::1]") == ["non-RFC5737 address: 2001:db8::1"]
    assert boundary_violations("Address: [2001:db8::1].") == ["non-RFC5737 address: 2001:db8::1"]
    assert boundary_violations("Address: [2001:db8::gg].") == ["invalid endpoint authority"]
    assert boundary_violations("host: [2001:db8::1]:5432") == ["non-RFC5737 address: 2001:db8::1"]
    assert boundary_violations("host: [2001:db8::1") == ["invalid endpoint authority"]
    assert boundary_violations("endpoint: http://[2001:db8::1/path") == [
        "invalid endpoint authority"
    ]
    assert boundary_violations("Connect to [2001:db8::1:443/path?target=10.0.0.7") == [
        "invalid endpoint authority"
    ]


def test_boundary_detector_deduplicates_raw_and_decoded_payload_findings() -> None:
    assert boundary_violations("endpoint: https://example.com/path/db.production.internal%20") == [
        "non-example hostname: db.production.internal"
    ]
    assert boundary_violations(
        "endpoint: https://example.com/?setting=GCP_PROJECT_ID=production-123%20"
    ) == ["GCP project identifier"]
    assert boundary_violations("endpoint: https://example.com/users/user%40private.internal") == [
        "non-example hostname: private.internal"
    ]
    assert boundary_violations("endpoint: https://example.com/users/user%2540private.internal") == [
        "non-example hostname: private.internal"
    ]
    assert boundary_violations("endpoint: https://example.com/users/api%2Eprivate.internal") == [
        "non-example hostname: api.private.internal"
    ]


@pytest.mark.parametrize("rounds", [1, 2, 3])
@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        ("http://privatehost:5432/path", "non-example hostname: privatehost"),
        ("//privatehost:5432/path", "non-example hostname: privatehost"),
        ("https://user:password@example.com/path", "credential URL userinfo"),
        ("//user:password@example.com/path", "credential URL userinfo"),
    ],
)
def test_boundary_detector_scans_recursively_decoded_authorities(
    rounds: int,
    authority: str,
    expected: str,
) -> None:
    encoded = authority
    for _round in range(rounds):
        encoded = quote(encoded, safe="")

    assert boundary_violations(f"endpoint: https://example.com/?target={encoded}") == [expected]


@pytest.mark.parametrize(
    "outer_url",
    [
        "https://example.com/redirect/https://privatehost:5432/path",
        "https://example.com/?target=//privatehost:5432/path",
        "https://example.com/#target=https://privatehost:5432/path",
    ],
)
def test_boundary_detector_scans_raw_nested_authorities(outer_url: str) -> None:
    assert boundary_violations(f"endpoint: {outer_url}") == ["non-example hostname: privatehost"]


def test_boundary_detector_bounds_url_payload_size() -> None:
    oversized_path = "a" * (MAX_URL_PAYLOAD_LENGTH + 1)
    assert boundary_violations(f"endpoint: https://example.com/{oversized_path}") == [
        "URL payload exceeds scan limit"
    ]
    nested_url = "//privatehost:5432"
    for _depth in range(6):
        nested_url = f"https://example.com/?target={nested_url}"
    assert boundary_violations(f"endpoint: {nested_url}") == [
        "URL authority nesting exceeds scan limit"
    ]


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
