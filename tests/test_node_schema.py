from infralink.core.schema import HostSchema, ServiceSchema


def test_host_schema_defaults_node_type():
    host = HostSchema(canonical_name="h1")
    assert host.node_type == "host"


def test_service_schema_defaults_node_type_and_canonical_name():
    service = ServiceSchema(name="postgres", group="db")
    assert service.node_type == "service"
    assert service.canonical_name == "postgres"
