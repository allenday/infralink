from infralink.core.registry import validate_role_slots
from infralink.core.schema import HostSchema, RoleConfig


def test_validate_role_slots_requires_binding():
    roles = {"wordpress": RoleConfig(services={}, slots={"db": {"type": "database"}})}
    hosts = {
        "uuid": HostSchema(canonical_name="h1", roles=["wordpress"], role_overrides={"wordpress": {}})
    }
    errors = validate_role_slots(hosts, roles)
    assert any("slot db" in e for e in errors)
