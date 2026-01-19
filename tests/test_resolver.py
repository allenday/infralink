"""Tests for resolver module."""

import pytest

from infralink.core.registry import Registry
from infralink.core.edges import EdgeSet
from infralink.core.resolver import EdgeResolver, ResolutionError


@pytest.fixture
def registry():
    """Create a test registry.

    UUID is the dictionary key (primary identifier).
    """
    return Registry.from_dict({
        "hosts": {
            # UUID is the key
            "d1b9e5d5-36b0-459d-a556-96622811fbd5": {
                "canonical_name": "prod-database",
                "status": "active",
                "tailscale_ip": "100.78.109.111",
                "public_ip": "91.99.122.86",
                "services": ["postgresql", "redis"],
            },
            "fa2b9872-d94c-4b20-a73a-57a205560769": {
                "canonical_name": "prod-app",
                "status": "active",
                "tailscale_ip": "100.69.66.115",
                "services": ["nginx", "app"],
                "roles": {"app-worker": {"concurrency": 10}},
            },
        }
    })


@pytest.fixture
def edges():
    """Create a test edge set."""
    return EdgeSet.from_dict({
        "edges": [
            {
                "id": "app-to-postgres",
                "type": "database",
                "from": {
                    "hosts": ["fa2b9872-d94c-4b20-a73a-57a205560769"],
                    "service": "app",
                },
                "to": {
                    "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                    "service": "postgresql",
                    "port": 5432,
                },
                "protocol": "postgresql+psycopg2",
            },
            {
                "id": "app-to-redis",
                "type": "queue",
                "from": {
                    "hosts": ["fa2b9872-d94c-4b20-a73a-57a205560769"],
                    "service": "app",
                },
                "to": {
                    "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                    "service": "redis",
                    "port": 6379,
                },
                "protocol": "redis",
            },
        ]
    })


class TestEdgeResolver:
    """Tests for EdgeResolver class."""

    def test_get_edge(self, registry, edges):
        """Test getting edge by ID."""
        resolver = EdgeResolver(registry, edges)

        edge = resolver.get_edge("app-to-postgres")
        assert edge.id == "app-to-postgres"

    def test_get_edge_not_found(self, registry, edges):
        """Test getting nonexistent edge."""
        resolver = EdgeResolver(registry, edges)

        with pytest.raises(ResolutionError) as exc_info:
            resolver.get_edge("nonexistent")
        assert "Edge not found" in str(exc_info.value)

    def test_get_target_host(self, registry, edges):
        """Test getting target host."""
        resolver = EdgeResolver(registry, edges)

        host = resolver.get_target_host("app-to-postgres")
        assert host.canonical_name == "prod-database"

    def test_get_target_ip(self, registry, edges):
        """Test getting target IP."""
        resolver = EdgeResolver(registry, edges)

        ip = resolver.get_target_ip("app-to-postgres")
        assert ip == "100.78.109.111"

        ip_public = resolver.get_target_ip("app-to-postgres", prefer="public")
        assert ip_public == "91.99.122.86"

    def test_get_target_port(self, registry, edges):
        """Test getting target port."""
        resolver = EdgeResolver(registry, edges)

        port = resolver.get_target_port("app-to-postgres")
        assert port == 5432

    def test_get_target_endpoint(self, registry, edges):
        """Test getting target endpoint."""
        resolver = EdgeResolver(registry, edges)

        endpoint = resolver.get_target_endpoint("app-to-postgres")
        assert endpoint == "100.78.109.111:5432"

    def test_get_url(self, registry, edges):
        """Test generating connection URL."""
        resolver = EdgeResolver(registry, edges)

        url = resolver.get_url(
            "app-to-postgres",
            user="myuser",
            password="mypass",
            database="mydb",
        )
        assert url == "postgresql+psycopg2://myuser:mypass@100.78.109.111:5432/mydb"

    def test_get_url_with_special_chars(self, registry, edges):
        """Test URL generation with special characters in password."""
        resolver = EdgeResolver(registry, edges)

        url = resolver.get_url(
            "app-to-postgres",
            user="myuser",
            password="pass@word!",
            database="mydb",
        )
        assert "pass%40word%21" in url

    def test_get_postgres_url(self, registry, edges):
        """Test PostgreSQL-specific URL generation."""
        resolver = EdgeResolver(registry, edges)

        url = resolver.get_postgres_url(
            "app-to-postgres",
            user="myuser",
            password="mypass",
            database="mydb",
        )
        assert url.startswith("postgresql+psycopg2://")
        assert "myuser:mypass" in url
        assert "100.78.109.111:5432" in url
        assert "/mydb" in url

    def test_get_redis_url(self, registry, edges):
        """Test Redis URL generation."""
        resolver = EdgeResolver(registry, edges)

        url = resolver.get_redis_url(
            "app-to-redis",
            password="mypass",
            db=1,
        )
        assert url == "redis://:mypass@100.78.109.111:6379/1"

    def test_to_template_context(self, registry, edges):
        """Test template context generation."""
        resolver = EdgeResolver(registry, edges)

        context = resolver.to_template_context("app-to-postgres")

        assert context["edge_id"] == "app-to-postgres"
        assert context["target_ip"] == "100.78.109.111"
        assert context["target_port"] == 5432
        assert context["target_service"] == "postgresql"
        assert context["target_host_name"] == "prod-database"

    def test_validate_all(self, registry, edges):
        """Test validating all edges."""
        resolver = EdgeResolver(registry, edges)

        errors = resolver.validate_all()
        assert errors == []

    def test_validate_all_with_missing_target(self, registry):
        """Test validation with missing target host."""
        edges = EdgeSet.from_dict({
            "edges": [
                {
                    "id": "broken-edge",
                    "type": "database",
                    "from": {"hosts": [], "service": "app"},
                    "to": {
                        "host": "nonexistent-uuid",
                        "service": "postgresql",
                        "port": 5432,
                    },
                }
            ]
        })
        resolver = EdgeResolver(registry, edges)

        errors = resolver.validate_all()
        assert len(errors) == 1
        assert "not found" in errors[0]
