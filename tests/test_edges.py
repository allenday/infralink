"""Tests for edges module."""

import pytest

from infralink.core.edges import Edge, EdgeSet
from infralink.core.schema import EdgeType, Criticality


@pytest.fixture
def sample_edge_data():
    """Sample edge data for testing."""
    return {
        "id": "app-to-postgres",
        "type": "database",
        "from": {
            "hosts": [
                "fa2b9872-d94c-4b20-a73a-57a205560769",
                "b1a554f8-76ed-4d98-91bb-f0fbfc2818d1",
            ],
            "service": "app-worker",
        },
        "to": {
            "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
            "service": "postgresql",
            "port": 5432,
        },
        "protocol": "postgresql+psycopg2",
        "metadata": {
            "criticality": "critical",
            "purpose": "Application database",
        },
    }


@pytest.fixture
def sample_edges_data(sample_edge_data):
    """Sample edges collection for testing."""
    return {
        "schema_version": "1.0",
        "edges": [
            sample_edge_data,
            {
                "id": "app-to-redis",
                "type": "queue",
                "from": {
                    "hosts": ["fa2b9872-d94c-4b20-a73a-57a205560769"],
                    "service": "app-worker",
                },
                "to": {
                    "host": "d1b9e5d5-36b0-459d-a556-96622811fbd5",
                    "service": "redis",
                    "port": 6379,
                },
                "protocol": "redis",
                "metadata": {
                    "criticality": "high",
                },
            },
            {
                "id": "monitoring-scrape",
                "type": "monitoring",
                "from": {
                    "hosts": "*",
                    "service": "prometheus",
                },
                "to": {
                    "host": "fa2b9872-d94c-4b20-a73a-57a205560769",
                    "service": "node-exporter",
                    "port": 9100,
                },
                "metadata": {
                    "criticality": "medium",
                },
            },
        ],
    }


class TestEdge:
    """Tests for Edge class."""

    def test_edge_properties(self, sample_edge_data):
        """Test basic edge properties."""
        edge = Edge(sample_edge_data)

        assert edge.id == "app-to-postgres"
        assert edge.type == EdgeType.DATABASE
        assert edge.target_host == "d1b9e5d5-36b0-459d-a556-96622811fbd5"
        assert edge.target_service == "postgresql"
        assert edge.target_port == 5432
        assert edge.protocol == "postgresql+psycopg2"

    def test_edge_criticality(self, sample_edge_data):
        """Test edge criticality."""
        edge = Edge(sample_edge_data)

        assert edge.criticality == Criticality.CRITICAL
        assert edge.is_critical

    def test_edge_source_hosts(self, sample_edge_data):
        """Test edge source hosts."""
        edge = Edge(sample_edge_data)

        assert len(edge.source_hosts) == 2
        assert "fa2b9872-d94c-4b20-a73a-57a205560769" in edge.source_hosts
        assert not edge.is_wildcard_source()

    def test_edge_matches_source(self, sample_edge_data):
        """Test source matching."""
        edge = Edge(sample_edge_data)

        assert edge.matches_source("fa2b9872-d94c-4b20-a73a-57a205560769")
        assert not edge.matches_source("nonexistent-uuid")


class TestEdgeSet:
    """Tests for EdgeSet class."""

    def test_edgeset_from_dict(self, sample_edges_data):
        """Test EdgeSet creation from dictionary."""
        edges = EdgeSet.from_dict(sample_edges_data)

        assert len(edges) == 3
        assert edges.schema_version == "1.0"

    def test_get_by_id(self, sample_edges_data):
        """Test edge lookup by ID."""
        edges = EdgeSet.from_dict(sample_edges_data)

        edge = edges.get("app-to-postgres")
        assert edge is not None
        assert edge.type == EdgeType.DATABASE

    def test_by_type(self, sample_edges_data):
        """Test filtering by type."""
        edges = EdgeSet.from_dict(sample_edges_data)

        database_edges = edges.by_type(EdgeType.DATABASE)
        assert len(database_edges) == 1

        queue_edges = edges.by_type(EdgeType.QUEUE)
        assert len(queue_edges) == 1

    def test_critical_edges(self, sample_edges_data):
        """Test getting critical edges."""
        edges = EdgeSet.from_dict(sample_edges_data)

        critical = edges.critical_edges()
        assert len(critical) == 1
        assert critical[0].id == "app-to-postgres"

    def test_targeting_host(self, sample_edges_data):
        """Test getting edges targeting a host."""
        edges = EdgeSet.from_dict(sample_edges_data)

        targeting_db = edges.targeting_host("d1b9e5d5-36b0-459d-a556-96622811fbd5")
        assert len(targeting_db) == 2

    def test_from_host(self, sample_edges_data):
        """Test getting edges from a host."""
        edges = EdgeSet.from_dict(sample_edges_data)

        from_app1 = edges.from_host("fa2b9872-d94c-4b20-a73a-57a205560769")
        assert len(from_app1) == 3  # 2 explicit + 1 wildcard

    def test_contains(self, sample_edges_data):
        """Test __contains__ method."""
        edges = EdgeSet.from_dict(sample_edges_data)

        assert "app-to-postgres" in edges
        assert "nonexistent" not in edges

    def test_wildcard_source(self, sample_edges_data):
        """Test wildcard source handling."""
        edges = EdgeSet.from_dict(sample_edges_data)

        monitoring = edges.get("monitoring-scrape")
        assert monitoring.is_wildcard_source()
        assert monitoring.source_hosts == []  # Empty for wildcard


class TestNewEdgeTypes:
    """Tests for extended edge types (ssh, security, smtp, irc, xmpp)."""

    def test_ssh_edge(self):
        """Test SSH edge type with username auth."""
        edge = Edge({
            "id": "bastion-to-worker-ssh",
            "type": "ssh",
            "from": {
                "hosts": ["aaaa-bbbb-cccc-dddd-eeee"],
                "service": "*",
            },
            "to": {
                "host": "ffff-gggg-hhhh-iiii-jjjj",
                "service": "ssh",
                "port": 22,
            },
            "protocol": "ssh",
            "auth": {
                "username": "devops",
            },
        })
        assert edge.type == EdgeType.SSH
        assert edge.target_port == 22

    def test_security_edge(self):
        """Test security edge type."""
        edge = Edge({
            "id": "nginx-tls-certs",
            "type": "security",
            "from": {
                "hosts": ["aaaa-bbbb-cccc-dddd-eeee"],
                "service": "nginx",
            },
            "to": {
                "host": "aaaa-bbbb-cccc-dddd-eeee",
                "service": "tls-certs",
                "port": 0,
            },
            "protocol": "internal",
        })
        assert edge.type == EdgeType.SECURITY

    def test_smtp_edge(self):
        """Test SMTP edge type."""
        edge = Edge({
            "id": "wordpress-to-postfix",
            "type": "smtp",
            "from": {
                "hosts": ["aaaa-bbbb-cccc-dddd-eeee"],
                "service": "wordpress",
            },
            "to": {
                "host": "ffff-gggg-hhhh-iiii-jjjj",
                "service": "postfix",
                "port": 587,
            },
            "protocol": "smtp",
        })
        assert edge.type == EdgeType.SMTP

    def test_irc_edge(self):
        """Test IRC edge type."""
        edge = Edge({
            "id": "anope-to-inspircd",
            "type": "irc",
            "from": {
                "hosts": ["aaaa-bbbb-cccc-dddd-eeee"],
                "service": "anope",
            },
            "to": {
                "host": "aaaa-bbbb-cccc-dddd-eeee",
                "service": "inspircd",
                "port": 7000,
            },
            "protocol": "s2s+tls",
        })
        assert edge.type == EdgeType.IRC

    def test_xmpp_edge(self):
        """Test XMPP edge type."""
        edge = Edge({
            "id": "jicofo-to-prosody",
            "type": "xmpp",
            "from": {
                "hosts": ["aaaa-bbbb-cccc-dddd-eeee"],
                "service": "jicofo",
            },
            "to": {
                "host": "aaaa-bbbb-cccc-dddd-eeee",
                "service": "prosody",
                "port": 5222,
            },
            "protocol": "xmpp",
        })
        assert edge.type == EdgeType.XMPP

    def test_auth_extended_fields(self):
        """Test extended auth fields (username, database, role, mount_path)."""
        edge = Edge({
            "id": "app-to-db",
            "type": "database",
            "from": {
                "hosts": ["aaaa-bbbb-cccc-dddd-eeee"],
                "service": "app",
            },
            "to": {
                "host": "ffff-gggg-hhhh-iiii-jjjj",
                "service": "postgresql",
                "port": 5432,
            },
            "protocol": "postgresql",
            "auth": {
                "username": "rw_user",
                "database": "myapp",
                "secret_ref": "postgresql_rw_password",
                "role": "rw",
            },
        })
        assert edge.type == EdgeType.DATABASE

    def test_auth_mount_path(self):
        """Test storage edge with mount_path auth."""
        edge = Edge({
            "id": "host-to-storagebox",
            "type": "storage",
            "from": {
                "hosts": ["aaaa-bbbb-cccc-dddd-eeee"],
                "service": "storagebox0",
            },
            "to": {
                "host": "ffff-gggg-hhhh-iiii-jjjj",
                "service": "sshfs",
                "port": 0,
            },
            "protocol": "mount",
            "auth": {
                "mount_path": "/mnt/storagebox0",
            },
        })
        assert edge.type == EdgeType.STORAGE
