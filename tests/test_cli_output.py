import json

from infralink.cli.output import error_envelope, ok_envelope


def test_ok_envelope_shape():
    payload = ok_envelope(
        "infralink validate",
        {"valid": True},
        [{"command": "infralink check", "description": "Run checks"}],
    )
    assert payload["ok"] is True
    assert payload["command"] == "infralink validate"
    assert payload["result"] == {"valid": True}
    assert payload["next_actions"][0]["command"] == "infralink check"


def test_error_envelope_shape():
    payload = error_envelope(
        "infralink validate",
        "boom",
        "VALIDATION_FAILED",
        "Fix it",
        [],
    )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert payload["fix"] == "Fix it"
