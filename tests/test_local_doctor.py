from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from infralink.host_readiness import HostReadinessProbe
from infralink.local_doctor import (
    LocalDoctorCollector,
    LocalDoctorResult,
    LatestResultStore,
    serve_latest_result,
)


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _probe(*, docker: bool = True) -> HostReadinessProbe:
    return HostReadinessProbe(
        reachable=True,
        hostname="node-1",
        machine_id="machine-id",
        commands={"git": True, "docker": docker, "tailscale": True, "jq": True, "bws": True},
        devops_account=True,
        devops_authorized_access=True,
        bws_config=True,
        self_deploy_dependencies=True,
        self_deploy_runtime=True,
        self_deploy_timer_enabled=True,
        self_deploy_timer_active=True,
        self_deploy_mode="v2_reconcile",
        self_deploy_reconcile_result="success",
        self_deploy_reconcile_exit_status=0,
        self_deploy_reconcile_active_state="inactive",
        self_deploy_reconcile_sub_state="dead",
        self_deploy_reconcile_exit_timestamp_monotonic=1,
        error="raw transport output must never be persisted",
    )


def test_collector_emits_a_stable_secret_free_result_from_readiness() -> None:
    result = LocalDoctorCollector(clock=lambda: NOW, freshness_seconds=60).collect(
        canonical_name="node-1", probe=_probe(docker=False)
    )

    payload = result.to_dict()

    assert payload["schema_version"] == "infralink.local-doctor/v1"
    assert payload["status"] == "unhealthy"
    assert payload["observed_at"] == "2026-08-09T12:00:00Z"
    assert payload["fresh_until"] == "2026-08-09T12:01:00Z"
    assert {item["id"] for item in payload["checks"]} >= {"docker", "self_deploy_reconcile"}
    assert "raw transport output" not in json.dumps(payload)
    assert "machine-id" not in json.dumps(payload)


def test_latest_result_store_replaces_the_previous_result_atomically(tmp_path: Path) -> None:
    path = tmp_path / "doctor" / "latest.json"
    store = LatestResultStore(path)
    healthy = LocalDoctorResult.healthy(now=NOW, freshness_seconds=60)
    unhealthy = LocalDoctorResult.unhealthy(now=NOW, freshness_seconds=60)

    store.write(healthy)
    store.write(unhealthy)

    assert store.load() == unhealthy
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "unhealthy"
    assert not list(path.parent.glob(".latest.json.*"))


@pytest.mark.parametrize(
    ("result", "now", "expected_status"),
    [
        (None, NOW, 503),
        (LocalDoctorResult.healthy(now=NOW, freshness_seconds=60), NOW, 200),
        (LocalDoctorResult.unhealthy(now=NOW, freshness_seconds=60), NOW, 503),
        (LocalDoctorResult.healthy(now=NOW, freshness_seconds=60), NOW + timedelta(seconds=61), 503),
    ],
)
def test_static_endpoint_reports_only_fresh_healthy_latest_result(
    tmp_path: Path, result: LocalDoctorResult | None, now: datetime, expected_status: int
) -> None:
    store = LatestResultStore(tmp_path / "latest.json")
    if result is not None:
        store.write(result)
    server = serve_latest_result("127.0.0.1", 0, store, clock=lambda: now)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/doctor/latest"
        if expected_status == 200:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - local test server
                assert response.status == 200
                assert json.loads(response.read())["status"] == "healthy"
        else:
            with pytest.raises(HTTPError) as error:
                urlopen(url, timeout=2)  # noqa: S310 - local test server
            assert error.value.code == 503
            payload = json.loads(error.value.read())
            assert payload["schema_version"] == "infralink.local-doctor/v1"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_static_endpoint_never_runs_the_collector_for_requests(tmp_path: Path) -> None:
    store = LatestResultStore(tmp_path / "latest.json")
    store.write(LocalDoctorResult.healthy(now=NOW, freshness_seconds=60))
    server = serve_latest_result("127.0.0.1", 0, store, clock=lambda: NOW)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/v1/doctor/latest", timeout=2
        ) as response:  # noqa: S310 - local test server
            assert response.status == 200
        with pytest.raises(HTTPError) as error:
            urlopen(
                f"http://127.0.0.1:{server.server_port}/not-found", timeout=2
            )  # noqa: S310 - local test server
        assert error.value.code == 404
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
