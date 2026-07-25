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
    ("auth_type", "secret_ref"),
    [
        ("none", "/bad"),
        ("password", "bad//ref"),
        ("basic", "bad:ref"),
        ("token", "bad}ref"),
        ("certificate", "bad ref"),
    ],
)
def test_every_nonnull_secret_reference_uses_shared_safe_syntax(auth_type, secret_ref):
    with pytest.raises(ValidationError, match="safe secret_ref"):
        AuthConfig(type=auth_type, secret_ref=secret_ref)


@pytest.mark.parametrize("auth_type", ["password", "basic", "token", "certificate"])
def test_credential_auth_accepts_hierarchical_secret_reference(auth_type):
    auth = AuthConfig(type=auth_type, secret_ref="production/db-password")

    assert auth.secret_ref == "production/db-password"


@pytest.mark.parametrize(
    "secret_ref",
    [
        "/production/db-password",
        "production/db-password/",
        "production//db-password",
        "production/./db-password",
        "production/../db-password",
        "production/.../db-password",
    ],
)
def test_secret_reference_rejects_empty_dot_and_traversal_segments(secret_ref):
    with pytest.raises(ValidationError, match="safe secret_ref"):
        AuthConfig(type="password", secret_ref=secret_ref)


@pytest.mark.parametrize("auth_type", ["password", "basic", "token"])
def test_credential_auth_requires_secret_reference(auth_type):
    with pytest.raises(ValidationError, match="requires secret_ref"):
        AuthConfig(type=auth_type)


def test_none_auth_forbids_secret_reference():
    with pytest.raises(ValidationError, match="none auth forbids secret_ref"):
        AuthConfig(type="none", secret_ref="unexpected")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"secret_ref": "client.cert_ref"},
        {"mount_path": "/run/certs/client.pem"},
    ],
)
def test_certificate_auth_accepts_safe_reference_or_absolute_mount(kwargs):
    auth = AuthConfig(type="certificate", **kwargs)

    assert auth.secret_ref == kwargs.get("secret_ref")
    assert auth.mount_path == kwargs.get("mount_path")


@pytest.mark.parametrize(
    "mount_path",
    [
        None,
        "",
        "relative/cert.pem",
        "/run/../secret",
        "/run/cert\n.pem",
        "/run/cert\x00.pem",
    ],
)
def test_certificate_auth_rejects_missing_or_unsafe_mount_without_reference(mount_path):
    with pytest.raises(ValidationError, match="certificate auth requires"):
        AuthConfig(type="certificate", mount_path=mount_path)


def test_certificate_auth_rejects_unsafe_mount_even_with_valid_reference():
    with pytest.raises(ValidationError, match="safe absolute mount_path"):
        AuthConfig(
            type="certificate",
            secret_ref="client-cert",
            mount_path="../client.pem",
        )
