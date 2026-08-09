from __future__ import annotations

import pytest
from pydantic import ValidationError

from infralink.core.schema import HostSchema


def _host(executor: object) -> dict[str, object]:
    return {
        "canonical_name": "example.internal",
        "bootstrap_executor": executor,
    }


def test_host_bootstrap_executor_requires_immutable_source_and_secret_references() -> None:
    host = HostSchema(**_host({
        "repository": "https://github.com/relax-dot-gg/infra-management.git",
        "revision": "a" * 40,
        "manifest": "ansible/executors/infralink-host-baseline.json",
        "bws_machine_token_ref": "host/bws-machine-token",
        "infra_read_deploy_key_ref": "host/infra-read-key",
    }))

    assert host.bootstrap_executor is not None
    assert host.bootstrap_executor.revision == "a" * 40


@pytest.mark.parametrize("field,value", [("revision", "main"), ("repository", "./infra-management")])
def test_host_bootstrap_executor_rejects_unpinned_or_local_sources(field: str, value: str) -> None:
    executor = {
        "repository": "https://github.com/relax-dot-gg/infra-management.git",
        "revision": "a" * 40,
        "manifest": "ansible/executors/infralink-host-baseline.json",
        "bws_machine_token_ref": "host/bws-machine-token",
        "infra_read_deploy_key_ref": "host/infra-read-key",
    }
    executor[field] = value

    with pytest.raises(ValidationError):
        HostSchema(**_host(executor))
