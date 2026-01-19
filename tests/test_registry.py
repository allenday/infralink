"""Tests for registry module."""

import pytest
from pathlib import Path

from infralink.core.registry import Registry, Host
from infralink.core.schema import HostStatus


@pytest.fixture
def sample_registry_data():
    """Sample registry data for testing.

    UUID is the dictionary key (primary identifier).
    canonical_name is the human-readable identifier inside the host data.
    """
    return {
        "hosts": {
            # UUID is the key
            "d1b9e5d5-36b0-459d-a556-96622811fbd5": {
                "canonical_name": "test-host-1",
                "status": "active",
                "group": "production",
                "cloud": "hetzner-cloud",
                "tailscale_ip": "100.78.109.111",
                "services": ["postgresql", "redis"],
            },
            "fa2b9872-d94c-4b20-a73a-57a205560769": {
                "canonical_name": "test-host-2",
                "status": "active",
                "group": "production",
                "cloud": "hetzner-cloud",
                "tailscale_ip": "100.69.66.115",
                "services": ["nginx", "app"],
            },
            "e1a2b3c4-d5e6-4f7a-8b9c-0d1e2f3a4b5c": {
                "canonical_name": "terminated-host",
                "status": "terminated",
                "group": "staging",
                "cloud": "gcp",
                "services": [],
            },
        }
    }


class TestHost:
    """Tests for Host class."""

    def test_host_properties(self, sample_registry_data):
        """Test basic host properties."""
        uuid = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
        data = sample_registry_data["hosts"][uuid]
        host = Host(uuid, data)

        assert host.uuid == "d1b9e5d5-36b0-459d-a556-96622811fbd5"
        assert host.uuid_prefix == "d1b9e5d5"
        assert host.canonical_name == "test-host-1"
        assert host.status == HostStatus.ACTIVE
        assert host.is_active
        assert host.group == "production"
        assert host.cloud == "hetzner-cloud"
        assert host.tailscale_ip == "100.78.109.111"

    def test_host_services(self, sample_registry_data):
        """Test host service queries."""
        uuid = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
        data = sample_registry_data["hosts"][uuid]
        host = Host(uuid, data)

        assert host.has_service("postgresql")
        assert host.has_service("redis")
        assert not host.has_service("nginx")

    def test_host_get_ip(self, sample_registry_data):
        """Test IP address retrieval with fallback."""
        uuid = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
        data = sample_registry_data["hosts"][uuid]
        host = Host(uuid, data)

        assert host.get_ip("tailscale") == "100.78.109.111"
        # Falls back to tailscale_ip when public_ip is not defined
        assert host.get_ip("public") == "100.78.109.111"


class TestRegistry:
    """Tests for Registry class."""

    def test_registry_from_dict(self, sample_registry_data):
        """Test registry creation from dictionary."""
        registry = Registry.from_dict(sample_registry_data)

        assert len(registry) == 3
        assert len(registry.active_hosts()) == 2

    def test_get_by_uuid(self, sample_registry_data):
        """Test host lookup by UUID."""
        registry = Registry.from_dict(sample_registry_data)

        host = registry.get_by_uuid("d1b9e5d5-36b0-459d-a556-96622811fbd5")
        assert host is not None
        assert host.canonical_name == "test-host-1"

    def test_get_by_uuid_prefix(self, sample_registry_data):
        """Test host lookup by UUID prefix."""
        registry = Registry.from_dict(sample_registry_data)

        host = registry.get_by_uuid_prefix("d1b9e5d5")
        assert host is not None
        assert host.canonical_name == "test-host-1"

    def test_get_by_name(self, sample_registry_data):
        """Test host lookup by name."""
        registry = Registry.from_dict(sample_registry_data)

        host = registry.get_by_name("test-host-1")
        assert host is not None
        assert host.uuid_prefix == "d1b9e5d5"

    def test_filter_by_status(self, sample_registry_data):
        """Test filtering by status."""
        registry = Registry.from_dict(sample_registry_data)

        active = registry.filter(status=HostStatus.ACTIVE)
        assert len(active) == 2

        terminated = registry.filter(status=HostStatus.TERMINATED)
        assert len(terminated) == 1

    def test_filter_by_group(self, sample_registry_data):
        """Test filtering by group."""
        registry = Registry.from_dict(sample_registry_data)

        production = registry.filter(group="production")
        assert len(production) == 2

        staging = registry.filter(group="staging")
        assert len(staging) == 1

    def test_filter_by_service(self, sample_registry_data):
        """Test filtering by service."""
        registry = Registry.from_dict(sample_registry_data)

        postgres_hosts = registry.hosts_with_service("postgresql")
        assert len(postgres_hosts) == 1
        assert postgres_hosts[0].canonical_name == "test-host-1"

    def test_groups(self, sample_registry_data):
        """Test getting unique groups."""
        registry = Registry.from_dict(sample_registry_data)

        groups = registry.groups()
        assert groups == {"production", "staging"}

    def test_clouds(self, sample_registry_data):
        """Test getting unique cloud providers."""
        registry = Registry.from_dict(sample_registry_data)

        clouds = registry.clouds()
        assert clouds == {"hetzner-cloud", "gcp"}

    def test_contains(self, sample_registry_data):
        """Test __contains__ method."""
        registry = Registry.from_dict(sample_registry_data)

        assert "d1b9e5d5-36b0-459d-a556-96622811fbd5" in registry
        assert "test-host-1" in registry
        assert "nonexistent" not in registry
