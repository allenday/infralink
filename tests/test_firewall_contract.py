from __future__ import annotations

import pytest
from pydantic import ValidationError

from infralink.firewall import FirewallPolicy


def test_policy_accepts_declared_default_deny_ingress_and_snat() -> None:
    policy = FirewallPolicy.model_validate(
        {
            "backend": "nftables",
            "mode": "default-deny",
            "management_ssh": {
                "port": 22,
                "interface": "eth0",
                "sources": ["203.0.113.0/24"],
            },
            "ingress": [
                {
                    "service": "reverse-proxy",
                    "protocol": "tcp",
                    "ports": [443],
                    "interface": "tailscale0",
                    "bind_address": "100.64.0.10",
                    "sources": ["100.64.0.0/10"],
                }
            ],
            "host_bridge_ingress": [
                {"service": "metrics-agent", "protocol": "tcp", "ports": [9100]}
            ],
            "egress_snat": [
                {
                    "source_cidr": "172.18.0.0/16",
                    "protocol": "tcp",
                    "ports": [443],
                    "to_source": "198.51.100.10",
                }
            ],
        }
    )

    assert policy.ingress[0].service == "reverse-proxy"
    assert policy.egress_snat[0].to_source == "198.51.100.10"


@pytest.mark.parametrize(
    "field,value",
    [
        ("bind_address", "100.64.0.10"),
        ("sources", ["100.64.0.0/10"]),
    ],
)
def test_tailnet_ingress_requires_tailscale_interface(field: str, value: object) -> None:
    ingress = {
        "service": "reverse-proxy",
        "protocol": "tcp",
        "ports": [443],
        "interface": "eth0",
        "bind_address": "198.51.100.10",
        "sources": ["203.0.113.0/24"],
    }
    ingress[field] = value

    with pytest.raises(ValidationError):
        FirewallPolicy.model_validate(
            {
                "backend": "nftables",
                "mode": "default-deny",
                "management_ssh": {
                    "port": 22,
                    "interface": "eth0",
                    "sources": ["203.0.113.0/24"],
                },
                "ingress": [ingress],
            }
        )


def test_policy_rejects_duplicate_ingress_socket_ownership() -> None:
    ingress = {
        "service": "reverse-proxy",
        "protocol": "tcp",
        "ports": [443],
        "interface": "tailscale0",
        "bind_address": "100.64.0.10",
        "sources": ["100.64.0.0/10"],
    }
    duplicate = {**ingress, "service": "other-proxy"}

    with pytest.raises(ValidationError, match="one service owner"):
        FirewallPolicy.model_validate(
            {
                "backend": "nftables",
                "mode": "default-deny",
                "management_ssh": {
                    "port": 22,
                    "interface": "eth0",
                    "sources": ["203.0.113.0/24"],
                },
                "ingress": [ingress, duplicate],
            }
        )


def test_policy_rejects_same_socket_across_different_bind_addresses() -> None:
    ingress = {
        "service": "reverse-proxy",
        "protocol": "tcp",
        "ports": [443],
        "interface": "tailscale0",
        "bind_address": "100.64.0.10",
        "sources": ["100.64.0.0/10"],
    }
    alternate_address = {**ingress, "service": "other-proxy", "bind_address": "100.64.0.11"}

    with pytest.raises(ValidationError, match="one service owner"):
        FirewallPolicy.model_validate(
            {
                "backend": "nftables",
                "mode": "default-deny",
                "management_ssh": {
                    "port": 22,
                    "interface": "eth0",
                    "sources": ["203.0.113.0/24"],
                },
                "ingress": [ingress, alternate_address],
            }
        )
