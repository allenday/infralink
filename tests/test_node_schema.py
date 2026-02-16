from infralink.core.schema import HostSchema, RoleConfig, ServiceSchema, SlotBinding


def test_host_schema_defaults_node_type():
    host = HostSchema(canonical_name="h1")
    assert host.node_type == "host"


def test_service_schema_defaults_node_type_and_canonical_name():
    service = ServiceSchema(name="postgres", group="db")
    assert service.node_type == "service"
    assert service.canonical_name == "postgres"


def test_role_slots_required_by_default():
    role = RoleConfig(services={}, slots={"db": {"type": "database"}})
    assert role.slots["db"].required is True


def test_slot_binding_schema():
    binding = SlotBinding(host="h1", service="postgres", role="rw")
    assert binding.host == "h1"
    assert binding.service == "postgres"
