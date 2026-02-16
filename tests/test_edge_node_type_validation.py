from infralink.cli.validate import _edge_node_type_error


def test_edge_node_type_service_to_service_ok():
    assert _edge_node_type_error(source_service="app") is None


def test_edge_node_type_host_to_service_ok():
    assert _edge_node_type_error(source_service=None) is None
