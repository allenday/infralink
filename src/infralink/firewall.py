"""Portable firewall declaration contracts for registry-owned host intent."""

from __future__ import annotations

import ipaddress
import re
from typing import Literal, Self, cast

from pydantic import Field, field_validator, model_validator

from infralink.observation.models import Port, StrictModel

_SERVICE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
_TAILNET = cast(ipaddress.IPv4Network, ipaddress.ip_network("100.64.0.0/10"))


def _canonical_network(value: str, *, specific: bool = True) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise ValueError("must be a canonical CIDR") from error
    if str(network) != value or (specific and network.prefixlen == 0):
        raise ValueError("must be a specific canonical CIDR")
    return value


def _unique_ports(value: list[int]) -> list[int]:
    if len(value) != len(set(value)):
        raise ValueError("ports must be unique")
    return value


def _service(value: str) -> str:
    if _SERVICE.fullmatch(value) is None:
        raise ValueError("must be a valid service name")
    return value


def _interface(value: str) -> str:
    if _INTERFACE.fullmatch(value) is None:
        raise ValueError("must be a valid interface name")
    return value


class IngressRule(StrictModel):
    """One declared externally reachable service socket."""

    service: str
    protocol: Literal["tcp", "udp"]
    ports: list[Port] = Field(min_length=1, max_length=64)
    interface: str
    bind_address: str
    sources: list[str] = Field(min_length=1, max_length=64)

    @field_validator("service")
    @classmethod
    def valid_service(cls, value: str) -> str:
        return _service(value)

    @field_validator("interface")
    @classmethod
    def valid_interface(cls, value: str) -> str:
        return _interface(value)

    @field_validator("ports")
    @classmethod
    def unique_ports(cls, value: list[int]) -> list[int]:
        return _unique_ports(value)

    @field_validator("bind_address")
    @classmethod
    def canonical_bind_address(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError("bind_address must be a canonical IP address") from error
        if address.is_unspecified or address.is_loopback or str(address) != value:
            raise ValueError("bind_address must be a specific canonical IP address")
        return value

    @field_validator("sources")
    @classmethod
    def canonical_sources(cls, value: list[str]) -> list[str]:
        normalized = [_canonical_network(item) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("sources must be unique")
        return normalized

    @model_validator(mode="after")
    def scope_matches_sources(self) -> Self:
        sources = [ipaddress.ip_network(source) for source in self.sources]
        bind_address = ipaddress.ip_address(self.bind_address)
        source_is_tailnet = [
            isinstance(source, ipaddress.IPv4Network) and source.subnet_of(_TAILNET)
            for source in sources
        ]
        bind_is_tailnet = bind_address.version == _TAILNET.version and bind_address in _TAILNET
        if self.interface == "tailscale0":
            if not all(source_is_tailnet) or not bind_is_tailnet:
                raise ValueError("tailscale ingress requires Tailnet source CIDRs and bind address")
        elif any(source_is_tailnet) or bind_is_tailnet:
            raise ValueError("Tailnet source CIDRs and bind addresses require tailscale0")
        return self


class HostBridgeIngress(StrictModel):
    """Allow a sibling container to reach a host-networked service."""

    service: str
    protocol: Literal["tcp", "udp"]
    ports: list[Port] = Field(min_length=1, max_length=64)

    @field_validator("service")
    @classmethod
    def valid_service(cls, value: str) -> str:
        return _service(value)

    @field_validator("ports")
    @classmethod
    def unique_ports(cls, value: list[int]) -> list[int]:
        return _unique_ports(value)


class ContainerEgressRule(StrictModel):
    """Allow one bridge-mode service to reach declared external sockets."""

    service: str
    protocol: Literal["tcp", "udp"]
    ports: list[Port] = Field(min_length=1, max_length=64)
    destinations: list[str] = Field(min_length=1, max_length=64)

    @field_validator("service")
    @classmethod
    def valid_service(cls, value: str) -> str:
        return _service(value)

    @field_validator("ports")
    @classmethod
    def unique_ports(cls, value: list[int]) -> list[int]:
        return _unique_ports(value)

    @field_validator("destinations")
    @classmethod
    def canonical_destinations(cls, value: list[str]) -> list[str]:
        normalized = [_canonical_network(item, specific=False) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("destinations must be unique")
        return normalized


class EgressSnatRule(StrictModel):
    """Pin selected container egress to one explicit IPv4 source address."""

    source_cidr: str
    protocol: Literal["tcp", "udp"]
    ports: list[Port] = Field(min_length=1, max_length=64)
    to_source: str

    @field_validator("source_cidr")
    @classmethod
    def canonical_ipv4_source(cls, value: str) -> str:
        _canonical_network(value)
        if ipaddress.ip_network(value).version != 4:
            raise ValueError("source_cidr must be an IPv4 CIDR")
        return value

    @field_validator("ports")
    @classmethod
    def unique_ports(cls, value: list[int]) -> list[int]:
        return _unique_ports(value)

    @field_validator("to_source")
    @classmethod
    def canonical_ipv4_to_source(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError("to_source must be a canonical IPv4 address") from error
        if (
            address.version != 4
            or address.is_unspecified
            or address.is_loopback
            or address.is_multicast
            or address.is_link_local
            or str(address) != value
        ):
            raise ValueError("to_source must be a specific canonical IPv4 address")
        return value


class ManagementSsh(StrictModel):
    """The explicitly declared management SSH listener.

    ``interface: any`` intentionally covers every host interface, including
    IPv4, IPv6, and Tailnet paths.  Other values name one concrete interface.
    """

    port: Port
    interface: str
    sources: list[str] = Field(min_length=1, max_length=64)

    @field_validator("interface")
    @classmethod
    def valid_interface(cls, value: str) -> str:
        if value == "any":
            return value
        return _interface(value)

    @field_validator("sources")
    @classmethod
    def canonical_sources(cls, value: list[str]) -> list[str]:
        normalized = [_canonical_network(item, specific=False) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("sources must be unique")
        return normalized


class FirewallPolicy(StrictModel):
    """Default-deny host firewall intent independent of a runtime backend."""

    backend: Literal["nftables"]
    mode: Literal["default-deny"]
    management_ssh: ManagementSsh
    ingress: list[IngressRule] = Field(default_factory=list, max_length=128)
    host_bridge_ingress: list[HostBridgeIngress] = Field(default_factory=list, max_length=128)
    container_egress: list[ContainerEgressRule] = Field(default_factory=list, max_length=128)
    egress_snat: list[EgressSnatRule] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def unique_service_port_ownership(self) -> Self:
        sockets: set[tuple[str, str, int]] = set()
        for rule in self.ingress:
            for port in rule.ports:
                ingress_socket = (rule.protocol, rule.bind_address, port)
                if ingress_socket in sockets:
                    raise ValueError("each ingress socket needs one service owner")
                sockets.add(ingress_socket)
        bridge_sockets: set[tuple[str, int]] = set()
        for bridge_rule in self.host_bridge_ingress:
            for port in bridge_rule.ports:
                bridge_socket = (bridge_rule.protocol, port)
                if bridge_socket in bridge_sockets:
                    raise ValueError("each bridge port needs one service owner")
                bridge_sockets.add(bridge_socket)
        egress_sockets: set[tuple[str, str, int, str]] = set()
        for egress_rule in self.container_egress:
            for port in egress_rule.ports:
                for destination in egress_rule.destinations:
                    egress_socket = (
                        egress_rule.service,
                        egress_rule.protocol,
                        port,
                        destination,
                    )
                    if egress_socket in egress_sockets:
                        raise ValueError("each container egress socket needs one service owner")
                    egress_sockets.add(egress_socket)
        snat_sockets: set[tuple[str, str, int]] = set()
        for snat_rule in self.egress_snat:
            for port in snat_rule.ports:
                snat_socket = (snat_rule.source_cidr, snat_rule.protocol, port)
                if snat_socket in snat_sockets:
                    raise ValueError("each egress SNAT port needs one source owner")
                snat_sockets.add(snat_socket)
        return self
