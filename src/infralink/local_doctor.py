"""Local, persisted host-convergence evidence for the Infralink Doctor agent."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Literal

from infralink.host_readiness import HostReadinessEvaluator, HostReadinessProbe


SCHEMA_VERSION = "infralink.local-doctor/v1"
LATEST_RESULT_PATH = "/v1/doctor/latest"
LocalStatus = Literal["healthy", "unhealthy", "unknown"]


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


@dataclass(frozen=True)
class LocalDoctorCheck:
    id: str
    required: bool
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "required": self.required, "passed": self.passed}


@dataclass(frozen=True)
class LocalDoctorResult:
    observed_at: datetime
    fresh_until: datetime
    status: LocalStatus
    checks: tuple[LocalDoctorCheck, ...]

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
        for item in raw_checks:
            if (
                not isinstance(item, dict)
                or set(item) != {"id", "required", "passed"}
                or not isinstance(item["id"], str)
                or not item["id"]
                or type(item["required"]) is not bool
                or type(item["passed"]) is not bool
            ):
                raise ValueError("invalid local Doctor check")
            checks.append(
                LocalDoctorCheck(
                    id=item["id"], required=item["required"], passed=item["passed"]
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
        return self.status == "healthy" and now.astimezone(timezone.utc) <= self.fresh_until


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
        require_reconcile: bool = True,
    ) -> LocalDoctorResult:
        readiness = HostReadinessEvaluator().evaluate(
            canonical_name=canonical_name,
            probe=probe,
            require_reconcile=require_reconcile,
        )
        now = self._clock().astimezone(timezone.utc)
        return LocalDoctorResult(
            observed_at=now,
            fresh_until=now + timedelta(seconds=self._freshness_seconds),
            status="healthy" if readiness.ready else "unhealthy",
            checks=tuple(
                LocalDoctorCheck(id=check.id, required=check.required, passed=check.passed)
                for check in readiness.checks
            ),
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
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def load(self) -> LocalDoctorResult:
        return LocalDoctorResult.from_dict(json.loads(self.path.read_text(encoding="utf-8")))


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
            if self.path != LATEST_RESULT_PATH:
                self.send_error(404)
                return
            try:
                result = store.load()
            except (OSError, ValueError, json.JSONDecodeError):
                result = LocalDoctorResult.unknown(now=clock())
            payload = json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")).encode()
            self.send_response(200 if result.is_fresh_healthy(now=clock()) else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((address, port), LatestResultHandler)
