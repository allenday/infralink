import pytest
from pydantic import ValidationError

from infralink.core.schema import AuthConfig, HostSchema, RoleConfig, ServiceSchema, SlotBinding


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


def test_host_schema_allows_extra_fields():
    host = HostSchema(canonical_name="h1", probe_path="/health", tls_certs=[{"name": "t1"}])
    assert host.canonical_name == "h1"


@pytest.mark.parametrize(
    "secret_ref",
    [None, "", " ", "bad}ref", "bad:ref", "bad@ref"],
)
def test_password_auth_requires_valid_nonempty_secret_reference(secret_ref):
    with pytest.raises(ValidationError, match="valid nonempty secret_ref"):
        AuthConfig(type="password", secret_ref=secret_ref)


def test_non_password_auth_preserves_optional_secret_reference_semantics():
    auth = AuthConfig(type="basic")

    assert auth.secret_ref is None
