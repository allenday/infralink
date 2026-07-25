"""Tests for resolver module."""

import pytest

from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry
from infralink.core.resolver import EdgeResolver, ResolutionError
from infralink.secrets import SecretValue

SECRET_EDGE_ID = "11111111-1111-4111-8111-111111111111"
DECLARED_AUTH_ID = "22222222-2222-4222-8222-222222222222"
OTLP_EDGE_ID = "33333333-3333-4333-8333-333333333333"
UNSAFE_SECRET_ID = "44444444-4444-4444-8444-444444444444"
CONTEXT_EDGE_ID = "55555555-5555-4555-8555-555555555555"
MISSING_PORT_ID = "66666666-6666-4666-8666-666666666666"


@pytest.fixture
def registry():
    """Create a test registry.

    UUID is the dictionary key (primary identifier).
    """
    return Registry.from_dict(
        {
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
        }
    )


@pytest.fixture
def edges():
    """Create a test edge set."""
    return EdgeSet.from_dict(
        {
            "edges": [
                {
                    "id": "9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10",
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
                    "id": "c8c1a6a4-55c6-4a1b-9c14-1a4e0f615d8f",
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
        }
    )


class TestEdgeResolver:
    """Tests for EdgeResolver class."""

    def test_get_edge(self, registry, edges):
        """Test getting edge by ID."""
        resolver = EdgeResolver(registry, edges)

        edge = resolver.get_edge("9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10")
        assert edge.id == "9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10"

    def test_get_edge_not_found(self, registry, edges):
        """Test getting nonexistent edge."""
        resolver = EdgeResolver(registry, edges)

        with pytest.raises(ResolutionError) as exc_info:
            resolver.get_edge("nonexistent")
        assert "Edge not found" in str(exc_info.value)

    def test_get_target_host(self, registry, edges):
        """Test getting target host."""
        resolver = EdgeResolver(registry, edges)

        host = resolver.get_target_host("9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10")
        assert host.canonical_name == "prod-database"

    def test_get_target_ip(self, registry, edges):
        """Test getting target IP."""
        resolver = EdgeResolver(registry, edges)

        ip = resolver.get_target_ip("9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10")
        assert ip == "100.78.109.111"

        ip_public = resolver.get_target_ip("9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10", prefer="public")
        assert ip_public == "91.99.122.86"

    def test_get_target_port(self, registry, edges):
        """Test getting target port."""
        resolver = EdgeResolver(registry, edges)

        port = resolver.get_target_port("9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10")
        assert port == 5432

    def test_get_target_endpoint(self, registry, edges):
        """Test getting target endpoint."""
        resolver = EdgeResolver(registry, edges)

        endpoint = resolver.get_target_endpoint("9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10")
        assert endpoint == "100.78.109.111:5432"

    def test_get_url(self, registry, edges):
        """Test generating connection URL."""
        resolver = EdgeResolver(registry, edges)

        with pytest.deprecated_call():
            url = resolver.get_url(
                "9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10",
                user="myuser",
                password="mypass",
                database="mydb",
            )
        assert url == "postgresql+psycopg2://myuser:mypass@100.78.109.111:5432/mydb"

    def test_get_url_with_special_chars(self, registry, edges):
        """Test URL generation with special characters in password."""
        resolver = EdgeResolver(registry, edges)

        with pytest.deprecated_call():
            url = resolver.get_url(
                "9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10",
                user="myuser",
                password="pass@word!",
                database="mydb",
            )
        assert "pass%40word%21" in url

    def test_get_postgres_url(self, registry, edges):
        """Test PostgreSQL-specific URL generation."""
        resolver = EdgeResolver(registry, edges)

        with pytest.deprecated_call():
            url = resolver.get_postgres_url(
                "9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10",
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

        with pytest.deprecated_call():
            url = resolver.get_redis_url(
                "c8c1a6a4-55c6-4a1b-9c14-1a4e0f615d8f",
                password="mypass",
                db=1,
            )
        assert url == "redis://:mypass@100.78.109.111:6379/1"

    @pytest.mark.parametrize(
        ("protocol", "port", "user", "database", "expected"),
        [
            (
                "postgresql+psycopg2",
                5432,
                "app user",
                "app/db",
                "postgresql+psycopg2://app%20user:${secret:db-password}"
                "@100.78.109.111:5432/app%2Fdb",
            ),
            (
                "mysql+pymysql",
                3306,
                "app@user",
                "app db",
                "mysql+pymysql://app%40user:${secret:db-password}@100.78.109.111:3306/app%20db",
            ),
            (
                "redis",
                6379,
                None,
                "2",
                "redis://:${secret:db-password}@100.78.109.111:6379/2",
            ),
        ],
    )
    def test_get_connection_template_uses_literal_placeholder_and_safe_coordinates(
        self,
        registry,
        protocol,
        port,
        user,
        database,
        expected,
    ):
        edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": SECRET_EDGE_ID,
                        "type": "database",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                            "service": "database",
                            "port": port,
                        },
                        "protocol": protocol,
                        "auth": {"type": "password", "secret_ref": "db-password"},
                    }
                ]
            }
        )

        result = EdgeResolver(registry, edges).get_connection_template(
            SECRET_EDGE_ID,
            user=user,
            database=database,
        )

        assert result == expected
        assert "${secret:db-password}" in result
        assert "%24%7Bsecret" not in result

    def test_get_connection_template_uses_declared_auth_coordinates(self, registry):
        edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": DECLARED_AUTH_ID,
                        "type": "database",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                            "service": "database",
                            "port": 5432,
                        },
                        "protocol": "postgres",
                        "auth": {
                            "type": "password",
                            "secret_ref": "db-password",
                            "username": "declared user",
                            "database": "declared/db",
                        },
                    }
                ]
            }
        )

        result = EdgeResolver(registry, edges).get_connection_template(DECLARED_AUTH_ID)

        assert result == (
            "postgres://declared%20user:${secret:db-password}@100.78.109.111:5432/declared%2Fdb"
        )

    def test_get_connection_template_without_secret_is_password_free(self, registry, edges):
        resolver = EdgeResolver(registry, edges)

        result = resolver.get_connection_template(
            "9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10",
            user="app user",
            database="app",
        )

        assert result == "postgresql+psycopg2://app%20user@100.78.109.111:5432/app"
        assert "secret" not in result

    def test_get_connection_template_returns_none_for_non_connection_protocol(self, registry):
        edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": OTLP_EDGE_ID,
                        "type": "telemetry",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                            "service": "collector",
                            "port": 4317,
                        },
                        "protocol": "otlp",
                    }
                ]
            }
        )

        assert EdgeResolver(registry, edges).get_connection_template(OTLP_EDGE_ID) is None

    @pytest.mark.parametrize(
        "secret_ref",
        ["bad}ref", "${nested}", "bad:ref", "bad ref", "bad@ref"],
    )
    def test_get_connection_template_rejects_unsafe_secret_reference(self, registry, secret_ref):
        edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": UNSAFE_SECRET_ID,
                        "type": "database",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                            "service": "database",
                            "port": 5432,
                        },
                        "protocol": "postgres",
                        "auth": {"type": "password", "secret_ref": secret_ref},
                    }
                ]
            }
        )

        with pytest.raises(ResolutionError, match="unsafe secret reference"):
            EdgeResolver(registry, edges).get_connection_template(UNSAFE_SECRET_ID)

    @pytest.mark.parametrize(
        "method_name,kwargs",
        [
            ("get_url", {"password": SecretValue("resolver-canary")}),
            ("get_redis_url", {"password": SecretValue("resolver-canary")}),
            (
                "get_postgres_url",
                {
                    "user": "user",
                    "password": SecretValue("resolver-canary"),
                    "database": "db",
                },
            ),
            (
                "get_mysql_url",
                {
                    "user": "user",
                    "password": SecretValue("resolver-canary"),
                    "database": "db",
                },
            ),
        ],
    )
    def test_compatibility_url_methods_require_explicit_reveal(
        self, registry, edges, method_name, kwargs
    ):
        resolver = EdgeResolver(registry, edges)
        edge_id = (
            "c8c1a6a4-55c6-4a1b-9c14-1a4e0f615d8f"
            if method_name == "get_redis_url"
            else "9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10"
        )

        with pytest.deprecated_call():
            with pytest.raises(TypeError) as exc_info:
                getattr(resolver, method_name)(edge_id, **kwargs)

        assert "resolver-canary" not in str(exc_info.value)

    def test_compatibility_url_method_accepts_explicitly_revealed_value(self, registry, edges):
        resolver = EdgeResolver(registry, edges)
        secret = SecretValue("revealed-password")

        with pytest.deprecated_call():
            result = resolver.get_postgres_url(
                "9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10",
                user="user",
                password=secret.reveal(),
                database="db",
            )

        assert "revealed-password" in result

    def test_template_context_rejects_secret_value_without_leaking(self, registry, edges):
        resolver = EdgeResolver(registry, edges)
        secret_edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": CONTEXT_EDGE_ID,
                        "type": "database",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                            "service": "database",
                            "port": 5432,
                        },
                        "auth": {"type": "password", "secret_ref": "db-password"},
                    }
                ]
            }
        )
        resolver = EdgeResolver(registry, secret_edges)

        with pytest.raises(TypeError) as exc_info:
            resolver.to_template_context(
                CONTEXT_EDGE_ID,
                {"db-password": SecretValue("context-canary")},  # type: ignore[dict-item]
            )

        assert "context-canary" not in str(exc_info.value)

    def test_template_context_accepts_explicitly_revealed_value(self, registry):
        edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": CONTEXT_EDGE_ID,
                        "type": "database",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                            "service": "database",
                            "port": 5432,
                        },
                        "auth": {"type": "password", "secret_ref": "db-password"},
                    }
                ]
            }
        )
        secret = SecretValue("explicit-secret")

        context = EdgeResolver(registry, edges).to_template_context(
            CONTEXT_EDGE_ID,
            {"db-password": secret.reveal()},
        )

        assert context["password"] == "explicit-secret"

    def test_template_context_rejects_none_secret_value(self, registry):
        edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": CONTEXT_EDGE_ID,
                        "type": "database",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                            "service": "database",
                            "port": 5432,
                        },
                        "auth": {"type": "password", "secret_ref": "db-password"},
                    }
                ]
            }
        )

        with pytest.raises(TypeError, match="plain strings"):
            EdgeResolver(registry, edges).to_template_context(
                CONTEXT_EDGE_ID,
                {"db-password": None},  # type: ignore[dict-item]
            )

    def test_missing_target_port_uses_resolution_error(self, registry):
        edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": MISSING_PORT_ID,
                        "type": "database",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                            "service": "database",
                        },
                        "protocol": "postgres",
                    }
                ]
            }
        )

        with pytest.raises(ResolutionError, match="No target port"):
            EdgeResolver(registry, edges).get_connection_template(MISSING_PORT_ID)

        with pytest.raises(ResolutionError, match="No target port"):
            EdgeResolver(registry, edges).to_template_context(MISSING_PORT_ID)

    def test_to_template_context(self, registry, edges):
        """Test template context generation."""
        resolver = EdgeResolver(registry, edges)

        context = resolver.to_template_context("9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10")

        assert context["edge_id"] == "9d8d0b1e-4e21-4f49-9c0c-2b1d9b9e6a10"
        assert context["target_ip"] == "100.78.109.111"
        assert context["target_port"] == 5432
        assert context["target_service"] == "postgresql"
        assert context["target_host_name"] == "prod-database"

    def test_validate_all(self, registry, edges):
        """Test validating all edges."""
        resolver = EdgeResolver(registry, edges)

        errors, warnings = resolver.validate_all()
        assert errors == []
        assert len(warnings) == 2
        assert all("missing an explicit healthcheck" in warning for warning in warnings)

    def test_validate_all_with_missing_target(self, registry):
        """Test validation with missing target host."""
        edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": "8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2",
                        "type": "database",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "nonexistent-uuid",
                            "service": "postgresql",
                            "port": 5432,
                        },
                        "healthcheck": {"type": "tcp"},
                    }
                ]
            }
        )
        resolver = EdgeResolver(registry, edges)

        errors, warnings = resolver.validate_all()
        assert len(errors) == 1
        assert "not found" in errors[0]
        assert warnings == []
