"""Explicitly opted-in hosted BWS metadata smoke test."""

from __future__ import annotations

import os

import pytest

from infralink.adapters.bws import BwsConfig, BwsSecretResolver
from infralink.secrets import SecretReference

pytestmark = pytest.mark.skipif(
    os.environ.get("INFRALINK_RUN_LIVE_BWS") != "1"
    or os.environ.get("GITHUB_EVENT_NAME") in {"pull_request", "pull_request_target"},
    reason="live BWS tests require explicit opt-in outside pull-request contexts",
)


def test_hosted_bws_audit_reads_metadata_only() -> None:
    project = os.environ.get("INFRALINK_LIVE_BWS_PROJECT")
    reference = os.environ.get("INFRALINK_LIVE_BWS_SECRET_REF")
    required = {
        "BWS_ACCESS_TOKEN": os.environ.get("BWS_ACCESS_TOKEN"),
        "BWS_ORGANIZATION_ID": os.environ.get("BWS_ORGANIZATION_ID"),
        "INFRALINK_LIVE_BWS_PROJECT": project,
        "INFRALINK_LIVE_BWS_SECRET_REF": reference,
    }
    if not all(required.values()):
        pytest.skip("all live BWS environment values are required")

    resolver = BwsSecretResolver(config=BwsConfig.from_env())
    audits = resolver.audit(
        [
            SecretReference(
                ref=str(reference),
                projects=(str(project),),
                locations=("live.opt_in",),
            )
        ]
    )

    assert len(audits) == 1
    assert (audits[0].ref, audits[0].project) == (reference, project)
