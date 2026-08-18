from __future__ import annotations

import pytest
from pydantic import ValidationError

from infralink.observation.models_v2 import EdgeScope
from infralink.observation.v2 import parse_v2_document

HOST_ID = "11111111-1111-4111-8111-111111111111"


def test_parse_v2_document_keeps_component_endpoint_edges_typed() -> None:
    parsed = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "proxy",
                    "components": [
                        {
                            "id": "nginx",
                            "endpoints": [{"id": "http", "protocol": "http", "port": 8080}],
                        },
                        {
                            "id": "application",
                            "endpoints": [{"id": "http", "protocol": "http", "port": 8000}],
                        },
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "api",
                    "host_id": HOST_ID,
                    "profile_id": "proxy",
                    "components": [{"slot_id": "nginx"}, {"slot_id": "application"}],
                }
            ],
            "component_edges": [
                {
                    "id": "nginx-to-application",
                    "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                    "target_endpoint_id": f"{HOST_ID}/api/application/http",
                }
            ],
        }
    )

    assert parsed.component_edges[0].scope is EdgeScope.INTRA_SERVICE
    assert parsed.service_instances[0].components[0].slot_id == "nginx"


def test_parse_v2_document_rejects_duplicate_component_edge_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate component edge id"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "component_edges": [
                    {
                        "id": "duplicate",
                        "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                        "target_endpoint_id": f"{HOST_ID}/api/application/http",
                    },
                    {
                        "id": "duplicate",
                        "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                        "target_endpoint_id": f"{HOST_ID}/api/application/http",
                    },
                ],
            }
        )
