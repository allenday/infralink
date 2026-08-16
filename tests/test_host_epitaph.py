from datetime import date

import pytest
from pydantic import ValidationError

from infralink.core.schema import HostSchema


def test_inactive_host_parses_typed_epitaph() -> None:
    host = HostSchema(
        canonical_name="retired-host",
        status="inactive",
        epitaph={
            "retired_at": "2026-08-16",
            "reason": "superseded by a managed replacement",
            "rollback_ref": "archive/retired-host-2026-08",
            "rollback_path": "hosts/retired-host",
        },
    )

    assert host.epitaph is not None
    assert host.epitaph.retired_at == date(2026, 8, 16)
    assert host.epitaph.rollback_ref == "archive/retired-host-2026-08"


def test_epitaph_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="unexpected"):
        HostSchema(
            canonical_name="retired-host",
            status="inactive",
            epitaph={
                "retired_at": "2026-08-16",
                "reason": "superseded by a managed replacement",
                "unexpected": "not a schema contract",
            },
        )
