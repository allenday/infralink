"""Local, persisted host-convergence evidence for the Infralink Doctor agent."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

from infralink.host_readiness import HostReadinessEvaluator, HostReadinessProbe

SCHEMA_VERSION = "infralink.local-doctor/v1"
LATEST_RESULT_PATH = "/v1/doctor/latest"
METRICS_PATH = "/metrics"
LocalStatus = Literal["healthy", "unhealthy", "unknown"]
LocalDoctorSeverity = Literal["error", "warning", "info"]
_CHECK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CHECK_IDS = frozenset(
    {
        "registry_layout",
        "ssh_reachable",
        "host_identity",
        "machine_id",
        "devops_account",
        "devops_authorized_access",
        "git",
        "docker",
        "tailscale",
        "jq",
        "bws_cli",
        "bws_config",
        "self_deploy_dependencies",
        "self_deploy_runtime",
        "self_deploy_timer",
        "self_deploy_reconcile",
        "controller_python",
        "legacy_compatibility",
        "firewall_default_deny",
        "firewall_management_ssh",
        "firewall_declared_ingress",
        "firewall_converged",
    }
)
_DETAIL_CODES = frozenset(
    {
        "ok",
        "readiness_check_failed",
        "legacy_check_unrecognized",
        "firewall_default_deny_undeclared",
        "firewall_default_deny_missing",
        "firewall_management_ssh_undeclared",
        "firewall_management_ssh_missing",
        "firewall_ingress_mismatch",
        "firewall_live_state_unavailable",
        "firewall_competing_input_hook",
        "firewall_not_converged",
    }
)
_CHECK_DETAIL_CODES = {
    check_id: frozenset({"ok", "readiness_check_failed"})
    for check_id in _CHECK_IDS
    if not check_id.startswith("firewall_")
}
_CHECK_DETAIL_CODES.update(
    {
        "legacy_compatibility": frozenset({"ok", "legacy_check_unrecognized"}),
        "firewall_default_deny": frozenset(
            {
                "ok",
                "firewall_default_deny_undeclared",
                "firewall_default_deny_missing",
                "firewall_live_state_unavailable",
                "firewall_competing_input_hook",
            }
        ),
        "firewall_management_ssh": frozenset(
            {
                "ok",
                "firewall_management_ssh_undeclared",
                "firewall_management_ssh_missing",
                "firewall_live_state_unavailable",
                "firewall_competing_input_hook",
            }
        ),
        "firewall_declared_ingress": frozenset(
            {
                "ok",
                "firewall_ingress_mismatch",
                "firewall_live_state_unavailable",
                "firewall_competing_input_hook",
            }
        ),
        "firewall_converged": frozenset(
            {
                "ok",
                "firewall_live_state_unavailable",
                "firewall_competing_input_hook",
                "firewall_not_converged",
            }
        ),
    }
)
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class LocalDoctorCheck:
    id: str
    required: bool
    passed: bool
    severity: LocalDoctorSeverity | None = None
    detail_code: str | None = None

    def __post_init__(self) -> None:
        if _CHECK_ID.fullmatch(self.id) is None or self.id not in _CHECK_IDS:
            raise ValueError("local Doctor check id is invalid")
        if type(self.required) is not bool or type(self.passed) is not bool:
            raise ValueError("local Doctor check booleans are invalid")
        expected_severity: LocalDoctorSeverity = (
            "info" if self.passed else "error" if self.required else "warning"
        )
        expected_detail_code = "ok" if self.passed else "readiness_check_failed"
        severity = self.severity or expected_severity
        detail_code = self.detail_code or expected_detail_code
        if severity not in {"error", "warning", "info"} or severity != expected_severity:
            raise ValueError("local Doctor check severity is invalid")
        if detail_code not in _DETAIL_CODES or detail_code not in _CHECK_DETAIL_CODES[self.id]:
            raise ValueError("local Doctor check detail code is invalid")
        if self.passed and detail_code != "ok":
            raise ValueError("passed local Doctor check detail code is invalid")
        if not self.passed and detail_code == "ok":
            raise ValueError("failed local Doctor check detail code is invalid")
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "detail_code", detail_code)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "required": self.required,
            "passed": self.passed,
            "severity": self.severity,
            "detail_code": self.detail_code,
        }


@dataclass(frozen=True)
class FirewallDeclaration:
    """Verified local firewall intent, containing only canonical rule fingerprints."""

    default_deny: bool
    management_ssh_rule_fingerprint: str | None
    ingress_rule_fingerprints: frozenset[str]

    def __post_init__(self) -> None:
        if type(self.default_deny) is not bool:
            raise ValueError("firewall default-deny declaration is invalid")
        if self.management_ssh_rule_fingerprint is not None:
            _validate_fingerprint(self.management_ssh_rule_fingerprint)
        for fingerprint in self.ingress_rule_fingerprints:
            _validate_fingerprint(fingerprint)


@dataclass(frozen=True)
class SshAllowedSignersTrustRoot:
    """Concrete SSH signing trust root for a local firewall declaration."""

    allowed_signers_path: Path
    principal: str = "infralink-local-firewall"
    namespace: str = "infralink.local-firewall"

    def __post_init__(self) -> None:
        if not self.principal or not self.namespace:
            raise ValueError("local firewall trust root is invalid")


def canonical_nft_rule_fingerprint(rule: object) -> str:
    """Return the stable fingerprint of one nftables rule without retaining its contents."""
    if not isinstance(rule, dict):
        raise ValueError("nftables rule is invalid")
    canonical = {
        field: rule[field] for field in ("family", "table", "chain", "expr") if field in rule
    }
    if set(canonical) != {"family", "table", "chain", "expr"}:
        raise ValueError("nftables rule is incomplete")
    import hashlib

    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_signed_firewall_declaration(
    path: Path, *, trust_root: SshAllowedSignersTrustRoot
) -> FirewallDeclaration:
    """Load a locally materialized declaration only after its detached signature verifies."""
    raw = path.read_bytes()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("signed firewall declaration is invalid") from error
    if not isinstance(envelope, dict) or set(envelope) != {
        "schema_version",
        "declaration",
        "signature",
    }:
        raise ValueError("signed firewall declaration is invalid")
    declaration = envelope["declaration"]
    signature = envelope["signature"]
    if (
        envelope["schema_version"] != "infralink.local-firewall/v1"
        or not isinstance(declaration, dict)
        or not isinstance(signature, str)
        or not signature
        or len(signature) > 4096
    ):
        raise ValueError("signed firewall declaration is invalid")
    payload = json.dumps(declaration, sort_keys=True, separators=(",", ":")).encode()
    if not _verify_ssh_signature(payload, signature, trust_root):
        raise ValueError("signed firewall declaration signature is invalid")
    if set(declaration) != {
        "default_deny",
        "management_ssh_rule_fingerprint",
        "ingress_rule_fingerprints",
    } or not isinstance(declaration["ingress_rule_fingerprints"], list):
        raise ValueError("signed firewall declaration is invalid")
    return FirewallDeclaration(
        default_deny=declaration["default_deny"],
        management_ssh_rule_fingerprint=declaration["management_ssh_rule_fingerprint"],
        ingress_rule_fingerprints=frozenset(declaration["ingress_rule_fingerprints"]),
    )


def _verify_ssh_signature(
    payload: bytes, signature: str, trust_root: SshAllowedSignersTrustRoot
) -> bool:
    if not trust_root.allowed_signers_path.is_file():
        return False
    descriptor, signature_path = tempfile.mkstemp(
        prefix=".infralink-firewall-signature.", text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(signature)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(signature_path, 0o600)
        completed = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(trust_root.allowed_signers_path),
                "-I",
                trust_root.principal,
                "-n",
                trust_root.namespace,
                "-s",
                signature_path,
            ],
            input=payload,
            capture_output=True,
            check=False,
            shell=False,
        )
        return completed.returncode == 0
    except OSError:
        return False
    finally:
        try:
            os.unlink(signature_path)
        except FileNotFoundError:
            pass


class NftablesFirewallEvaluator:
    """Read-only comparison of signed firewall intent against the local nftables ruleset."""

    def __init__(self, *, command_runner: Callable[[list[str]], str] | None = None) -> None:
        self._command_runner = command_runner or _run_nftables

    def evaluate(self, declaration: FirewallDeclaration) -> tuple[LocalDoctorCheck, ...]:
        try:
            raw_ruleset = self._command_runner(["nft", "--json", "list", "ruleset"])
            live_default_deny, live_accepts = _nftables_state(json.loads(raw_ruleset))
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
            return _unavailable_firewall_checks()
        if live_default_deny is None:
            return _competing_input_hook_checks()

        expected_accepts = set(declaration.ingress_rule_fingerprints)
        if declaration.management_ssh_rule_fingerprint is not None:
            expected_accepts.add(declaration.management_ssh_rule_fingerprint)
        default_deny = LocalDoctorCheck(
            id="firewall_default_deny",
            required=True,
            passed=declaration.default_deny and live_default_deny,
            severity="info" if declaration.default_deny and live_default_deny else "error",
            detail_code="ok"
            if declaration.default_deny and live_default_deny
            else "firewall_default_deny_undeclared"
            if not declaration.default_deny
            else "firewall_default_deny_missing",
        )
        management_ssh = LocalDoctorCheck(
            id="firewall_management_ssh",
            required=True,
            passed=(
                declaration.management_ssh_rule_fingerprint is not None
                and declaration.management_ssh_rule_fingerprint in live_accepts
            ),
            severity="info"
            if (
                declaration.management_ssh_rule_fingerprint is not None
                and declaration.management_ssh_rule_fingerprint in live_accepts
            )
            else "error",
            detail_code="ok"
            if (
                declaration.management_ssh_rule_fingerprint is not None
                and declaration.management_ssh_rule_fingerprint in live_accepts
            )
            else "firewall_management_ssh_undeclared"
            if declaration.management_ssh_rule_fingerprint is None
            else "firewall_management_ssh_missing",
        )
        ingress = LocalDoctorCheck(
            id="firewall_declared_ingress",
            required=True,
            passed=live_accepts == expected_accepts,
            severity="info" if live_accepts == expected_accepts else "error",
            detail_code="ok" if live_accepts == expected_accepts else "firewall_ingress_mismatch",
        )
        converged = all(check.passed for check in (default_deny, management_ssh, ingress))
        return (
            default_deny,
            management_ssh,
            ingress,
            LocalDoctorCheck(
                id="firewall_converged",
                required=True,
                passed=converged,
                severity="info" if converged else "error",
                detail_code="ok" if converged else "firewall_not_converged",
            ),
        )


def _validate_fingerprint(value: object) -> None:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError("firewall rule fingerprint is invalid")


def _run_nftables(argv: list[str]) -> str:
    completed = subprocess.run(argv, text=True, capture_output=True, check=False, shell=False)
    if completed.returncode != 0:
        raise OSError("nftables query failed")
    return completed.stdout


def _nftables_state(value: object) -> tuple[bool | None, set[str]]:
    if not isinstance(value, dict) or not isinstance(value.get("nftables"), list):
        raise ValueError("nftables JSON is invalid")
    input_chains: set[tuple[str, str, str]] = set()
    for item in value["nftables"]:
        chain = item.get("chain") if isinstance(item, dict) else None
        if (
            isinstance(chain, dict)
            and chain.get("hook") == "input"
            and isinstance(chain.get("family"), str)
            and isinstance(chain.get("table"), str)
            and isinstance(chain.get("name"), str)
        ):
            input_chains.add((chain["family"], chain["table"], chain["name"]))
    if len(input_chains) != 1:
        return None, set()
    input_chain = next(iter(input_chains))
    chain = next(
        item["chain"]
        for item in value["nftables"]
        if isinstance(item, dict)
        and isinstance(item.get("chain"), dict)
        and (item["chain"].get("family"), item["chain"].get("table"), item["chain"].get("name"))
        == input_chain
    )
    if not isinstance(chain, dict) or chain.get("policy") != "drop":
        return None, set()
    live_default_deny = True
    accept_rules: set[str] = set()
    for item in value["nftables"]:
        rule = item.get("rule") if isinstance(item, dict) else None
        if not isinstance(rule, dict):
            continue
        location = (rule.get("family"), rule.get("table"), rule.get("chain"))
        if location in input_chains and _is_accept_rule(rule):
            accept_rules.add(canonical_nft_rule_fingerprint(rule))
    return live_default_deny, accept_rules


def _is_accept_rule(rule: dict[str, object]) -> bool:
    expressions = rule.get("expr")
    return isinstance(expressions, list) and any(
        isinstance(expression, dict) and "accept" in expression for expression in expressions
    )


def _unavailable_firewall_checks() -> tuple[LocalDoctorCheck, ...]:
    return _failed_firewall_checks("firewall_live_state_unavailable")


def _competing_input_hook_checks() -> tuple[LocalDoctorCheck, ...]:
    return _failed_firewall_checks("firewall_competing_input_hook")


def _failed_firewall_checks(detail_code: str) -> tuple[LocalDoctorCheck, ...]:
    checks = tuple(
        LocalDoctorCheck(
            id=check_id,
            required=True,
            passed=False,
            severity="error",
            detail_code=detail_code,
        )
        for check_id in (
            "firewall_default_deny",
            "firewall_management_ssh",
            "firewall_declared_ingress",
        )
    )
    return checks + (
        LocalDoctorCheck(
            id="firewall_converged",
            required=True,
            passed=False,
            severity="error",
            detail_code=detail_code,
        ),
    )


@dataclass(frozen=True)
class LocalDoctorResult:
    observed_at: datetime
    fresh_until: datetime
    status: LocalStatus
    checks: tuple[LocalDoctorCheck, ...]

    def __post_init__(self) -> None:
        observed_at = _utc(self.observed_at)
        fresh_until = _utc(self.fresh_until)
        if fresh_until < observed_at:
            raise ValueError("local Doctor result expires before observation")
        if self.status not in {"healthy", "unhealthy", "unknown"}:
            raise ValueError("invalid local Doctor status")
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("duplicate local Doctor check id")
        if self.status == "healthy" and any(
            check.required and not check.passed for check in self.checks
        ):
            raise ValueError("healthy local Doctor result has a failed required check")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "fresh_until", fresh_until)

    @classmethod
    def healthy(cls, *, now: datetime, freshness_seconds: int) -> LocalDoctorResult:
        return cls(now, now + timedelta(seconds=freshness_seconds), "healthy", ())

    @classmethod
    def unhealthy(cls, *, now: datetime, freshness_seconds: int) -> LocalDoctorResult:
        return cls(now, now + timedelta(seconds=freshness_seconds), "unhealthy", ())

    @classmethod
    def unknown(cls, *, now: datetime) -> LocalDoctorResult:
        return cls(now, now, "unknown", ())

    @classmethod
    def from_dict(cls, value: object) -> LocalDoctorResult:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "observed_at",
            "fresh_until",
            "status",
            "checks",
        }:
            raise ValueError("invalid local Doctor result")
        if value["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported local Doctor result schema")
        observed_at = _parse_timestamp(value["observed_at"])
        fresh_until = _parse_timestamp(value["fresh_until"])
        if fresh_until < observed_at:
            raise ValueError("local Doctor result expires before observation")
        if value["status"] not in {"healthy", "unhealthy", "unknown"}:
            raise ValueError("invalid local Doctor status")
        raw_checks = value["checks"]
        if not isinstance(raw_checks, list):
            raise ValueError("local Doctor checks must be a list")
        checks: list[LocalDoctorCheck] = []
        unknown_legacy_checks: list[dict[str, object]] = []
        for item in raw_checks:
            if (
                not isinstance(item, dict)
                or set(item)
                not in (
                    {"id", "required", "passed"},
                    {"id", "required", "passed", "severity", "detail_code"},
                )
                or not isinstance(item["id"], str)
                or not item["id"]
                or type(item["required"]) is not bool
                or type(item["passed"]) is not bool
                or ("severity" in item and item["severity"] not in {"error", "warning", "info"})
                or ("detail_code" in item and item["detail_code"] not in _DETAIL_CODES)
            ):
                raise ValueError("invalid local Doctor check")
            if set(item) == {"id", "required", "passed"} and item["id"] not in _CHECK_IDS:
                unknown_legacy_checks.append(item)
                continue
            checks.append(
                LocalDoctorCheck(
                    id=item["id"],
                    required=item["required"],
                    passed=item["passed"],
                    severity=item.get("severity"),
                    detail_code=item.get("detail_code"),
                )
            )
        if unknown_legacy_checks:
            required = any(item["required"] for item in unknown_legacy_checks)
            passed = all(item["passed"] for item in unknown_legacy_checks)
            checks.append(
                LocalDoctorCheck(
                    id="legacy_compatibility",
                    required=required,
                    passed=passed,
                    severity="info" if passed else "error" if required else "warning",
                    detail_code="ok" if passed else "legacy_check_unrecognized",
                )
            )
        return cls(
            observed_at=observed_at,
            fresh_until=fresh_until,
            status=value["status"],
            checks=tuple(checks),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "observed_at": _timestamp(self.observed_at),
            "fresh_until": _timestamp(self.fresh_until),
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
        }

    def is_fresh_healthy(self, *, now: datetime) -> bool:
        return self.status == "healthy" and self.is_fresh(now=now)

    def is_fresh(self, *, now: datetime) -> bool:
        return _utc(now) < self.fresh_until


class LocalDoctorCollector:
    """Convert an injected local readiness probe into secret-free evidence."""

    def __init__(self, *, clock: Callable[[], datetime], freshness_seconds: int) -> None:
        if freshness_seconds <= 0:
            raise ValueError("freshness_seconds must be positive")
        self._clock = clock
        self._freshness_seconds = freshness_seconds

    def collect(
        self,
        *,
        canonical_name: str,
        probe: HostReadinessProbe,
        firewall_declaration_path: Path,
        firewall_trust_root: SshAllowedSignersTrustRoot,
        firewall_evaluator: NftablesFirewallEvaluator | None = None,
        require_reconcile: bool = True,
    ) -> LocalDoctorResult:
        firewall_declaration = load_signed_firewall_declaration(
            firewall_declaration_path, trust_root=firewall_trust_root
        )
        readiness = HostReadinessEvaluator().evaluate(
            canonical_name=canonical_name,
            probe=probe,
            require_reconcile=require_reconcile,
        )
        now = self._clock().astimezone(timezone.utc)
        checks = tuple(
            [
                LocalDoctorCheck(
                    id=check.id,
                    required=check.required,
                    passed=check.passed,
                )
                for check in readiness.checks
            ]
            + list(
                (firewall_evaluator or NftablesFirewallEvaluator()).evaluate(firewall_declaration)
            )
        )
        return LocalDoctorResult(
            observed_at=now,
            fresh_until=now + timedelta(seconds=self._freshness_seconds),
            status="healthy"
            if all(not check.required or check.passed for check in checks)
            else "unhealthy",
            checks=checks,
        )


class LatestResultStore:
    """Persist and load one complete local Doctor result without partial reads."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, result: LocalDoctorResult) -> None:
        self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent, text=True
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(result.to_dict(), stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o640)
            os.replace(temporary, self.path)
            self._sync_parent()
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def load(self) -> LocalDoctorResult:
        return LocalDoctorResult.from_dict(json.loads(self.path.read_text(encoding="utf-8")))

    def _sync_parent(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(self.path.parent, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def serve_latest_result(
    address: str,
    port: int,
    store: LatestResultStore,
    *,
    clock: Callable[[], datetime],
) -> ThreadingHTTPServer:
    """Build a static latest-result server; request handling never runs checks."""

    class LatestResultHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path not in {LATEST_RESULT_PATH, METRICS_PATH}:
                self.send_error(404)
                return
            try:
                result = store.load()
            except (OSError, ValueError, json.JSONDecodeError):
                result = LocalDoctorResult.unknown(now=clock())
            if self.path == METRICS_PATH:
                payload = _prometheus_metrics(result, now=clock()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            payload = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")).encode()
            self.send_response(200 if result.is_fresh_healthy(now=clock()) else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((address, port), LatestResultHandler)


def _prometheus_metrics(result: LocalDoctorResult, *, now: datetime) -> str:
    """Render fixed-cardinality metrics from already-persisted Doctor evidence."""
    lines = [
        "# HELP infralink_local_doctor_status Latest local Doctor status, one series per status.",
        "# TYPE infralink_local_doctor_status gauge",
        *(
            f'infralink_local_doctor_status{{status="{status}"}} {int(result.status == status)}'
            for status in ("healthy", "unhealthy", "unknown")
        ),
        "# HELP infralink_local_doctor_fresh Whether the persisted Doctor evidence is fresh.",
        "# TYPE infralink_local_doctor_fresh gauge",
        f"infralink_local_doctor_fresh {int(result.is_fresh(now=now))}",
        "# HELP infralink_local_doctor_converged Whether current persisted evidence is fresh and healthy.",
        "# TYPE infralink_local_doctor_converged gauge",
        f"infralink_local_doctor_converged {int(result.is_fresh_healthy(now=now))}",
        "# HELP infralink_local_doctor_check Latest passed state for each closed Doctor check.",
        "# TYPE infralink_local_doctor_check gauge",
    ]
    lines.extend(
        "infralink_local_doctor_check"
        f'{{check_id="{check.id}",required="{str(check.required).lower()}",'
        f'severity="{check.severity}",detail_code="{check.detail_code}"}} {int(check.passed)}'
        for check in result.checks
    )
    return "\n".join(lines) + "\n"
