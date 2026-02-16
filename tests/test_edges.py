"""Tests for edges module."""

import pytest

from infralink.core.edges import Edge, EdgeSet
from infralink.core.schema import EdgeType, Criticality


@pytest.fixture
def sample_edge_data():
    """Sample edge data for testing."""
    return {
        "id": "e28f39b6-8389-45b1-93bd-579a18388df2",
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
                "id": "c1d51ce4-083e-4910-ae0f-f98b7b342b1c",
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
                "id": "aa3b8439-563c-4deb-a020-833da41a6b37",
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

        assert edge.id == "e28f39b6-8389-45b1-93bd-579a18388df2"
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

        edge = edges.get("e28f39b6-8389-45b1-93bd-579a18388df2")
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
        assert critical[0].id == "e28f39b6-8389-45b1-93bd-579a18388df2"

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

        assert "e28f39b6-8389-45b1-93bd-579a18388df2" in edges
        assert "nonexistent-0000-0000-0000-000000000000" not in edges

    def test_wildcard_source(self, sample_edges_data):
        """Test wildcard source handling."""
        edges = EdgeSet.from_dict(sample_edges_data)

        monitoring = edges.get("aa3b8439-563c-4deb-a020-833da41a6b37")
        assert monitoring.is_wildcard_source()
        assert monitoring.source_hosts == []  # Empty for wildcard


@pytest.mark.parametrize(
    "edge_data, expected_type",
    [
        (
            {
                "id": "2d2f78ba-458f-48f9-9959-eca8b5600fb4",
                "type": "ssh",
                "from": {"hosts": ["aaaa-bbbb-cccc-dddd-eeee"], "service": "*"},
                "to": {
                    "host": "ffff-gggg-hhhh-iiii-jjjj",
                    "service": "ssh",
                    "port": 22,
                },
                "protocol": "ssh",
                "auth": {"username": "devops"},
            },
            EdgeType.SSH,
        ),
        (
            {
                "id": "a394b8dd-bcea-47f4-a2f8-7498cc1bd5ab",
                "type": "security",
                "from": {"hosts": ["aaaa-bbbb-cccc-dddd-eeee"], "service": "nginx"},
                "to": {
                    "host": "aaaa-bbbb-cccc-dddd-eeee",
                    "service": "tls-certs",
                    "port": 0,
                },
                "protocol": "internal",
            },
            EdgeType.SECURITY,
        ),
        (
            {
                "id": "60fce49c-3df0-4433-981b-47ca3f848115",
                "type": "smtp",
                "from": {"hosts": ["aaaa-bbbb-cccc-dddd-eeee"], "service": "wordpress"},
                "to": {
                    "host": "ffff-gggg-hhhh-iiii-jjjj",
                    "service": "postfix",
                    "port": 587,
                },
                "protocol": "smtp",
            },
            EdgeType.SMTP,
        ),
        (
            {
                "id": "6c63aef6-841d-4354-a404-9ff2361e9e35",
                "type": "irc",
                "from": {"hosts": ["aaaa-bbbb-cccc-dddd-eeee"], "service": "anope"},
                "to": {
                    "host": "aaaa-bbbb-cccc-dddd-eeee",
                    "service": "inspircd",
                    "port": 7000,
                },
                "protocol": "s2s+tls",
            },
            EdgeType.IRC,
        ),
        (
            {
                "id": "75b5de43-573f-4243-94b3-1195acfe9fbd",
                "type": "xmpp",
                "from": {"hosts": ["aaaa-bbbb-cccc-dddd-eeee"], "service": "jicofo"},
                "to": {
                    "host": "aaaa-bbbb-cccc-dddd-eeee",
                    "service": "prosody",
                    "port": 5222,
                },
                "protocol": "xmpp",
            },
            EdgeType.XMPP,
        ),
    ],
)
def test_edge_types_supported(edge_data, expected_type):
    """Ensure new edge types validate and parse correctly."""
    edge = Edge(edge_data)
    assert edge.type == expected_type


def test_auth_extended_fields():
    """Test extended auth fields (username, database, role)."""
    edge = Edge(
        {
            "id": "fe33d192-8b6a-4b73-a269-eb64814c72a0",
            "type": "database",
            "from": {"hosts": ["aaaa-bbbb-cccc-dddd-eeee"], "service": "app"},
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
        }
    )
    assert edge.type == EdgeType.DATABASE


def test_auth_mount_path():
    """Test storage edge with mount_path auth."""
    edge = Edge(
        {
            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "type": "storage",
            "from": {"hosts": ["aaaa-bbbb-cccc-dddd-eeee"], "service": "storagebox0"},
            "to": {
                "host": "ffff-gggg-hhhh-iiii-jjjj",
                "service": "sshfs",
                "port": 0,
            },
            "protocol": "mount",
            "auth": {"mount_path": "/mnt/storagebox0"},
        }
    )
    assert edge.type == EdgeType.STORAGE
