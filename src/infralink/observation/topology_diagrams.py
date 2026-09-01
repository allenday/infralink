"""Deterministic declaration-only diagram renderers for observation v2 topology."""

from __future__ import annotations

import json
from collections import defaultdict
from hashlib import sha256
from typing import Literal

from infralink.observation.topology import V2TopologyNode, V2TopologyProjection

_MAX_RENDERED_BYTES = 1_048_576

__all__ = ["V2TopologyRenderBoundsError", "render_v2_dot", "render_v2_mermaid"]


class V2TopologyRenderBoundsError(ValueError):
    """A rendered declaration-only diagram exceeds the encoded output limit."""


def render_v2_mermaid(projection: V2TopologyProjection) -> str:
    """Render a projection as inline Mermaid source without runtime or address data."""

    lines = [
        "flowchart LR",
        "%% infralink.topology.schema_version=" + _metadata(projection.schema_version),
    ]
    _render_mermaid_ownership(lines, projection)
    for edge in sorted(projection.edges, key=lambda current: current.id):
        lines.extend(
            (
                "%% infralink.edge.id=" + _metadata(edge.id),
                f"{_endpoint_identifier(edge.source_endpoint_id)} -->|{_mermaid_label(_edge_label(edge.id, edge.scope.value))}| "
                f"{_endpoint_identifier(edge.target_endpoint_id)}",
            )
        )
    return _finalize_render(lines)


def render_v2_dot(projection: V2TopologyProjection) -> str:
    """Render a projection as Graphviz DOT source without runtime or address data."""

    lines = [
        'digraph "infralink_observation_v2" {',
        '    graph [rankdir="LR"];',
        '    node [shape="box"];',
    ]
    _render_dot_ownership(lines, projection)
    for edge in sorted(projection.edges, key=lambda current: current.id):
        lines.append(
            f'    "{_endpoint_identifier(edge.source_endpoint_id)}" -> '
            f'"{_endpoint_identifier(edge.target_endpoint_id)}" '
            f"[id={_dot_string(edge.id)}, label={_dot_string(_edge_label(edge.id, edge.scope.value))}];"
        )
    lines.append("}")
    return _finalize_render(lines)


def _render_mermaid_ownership(lines: list[str], projection: V2TopologyProjection) -> None:
    for host_id, services in _ownership_tree(projection).items():
        lines.extend(
            (
                "%% infralink.host.id=" + _metadata(host_id),
                f"subgraph {_identifier('host', host_id)}[{_mermaid_label(f'host {host_id}')}]",
            )
        )
        for service_id, components in services.items():
            service_ref = f"{host_id}/{service_id}"
            lines.extend(
                (
                    "    %% infralink.service.id=" + _metadata(service_ref),
                    f"    subgraph {_identifier('service', service_ref)}[{_mermaid_label(f'service {service_id}')}]",
                )
            )
            for component_id, nodes in components.items():
                component_ref = f"{service_ref}/{component_id}"
                lines.extend(
                    (
                        "        %% infralink.component.id=" + _metadata(component_ref),
                        f"        subgraph {_identifier('component', component_ref)}"
                        f"[{_mermaid_label(f'component {component_id}')}]",
                    )
                )
                for node in nodes:
                    lines.extend(
                        (
                            "            %% infralink.node.id=" + _metadata(node.id),
                            f"            {_endpoint_identifier(node.id)}[{_mermaid_label(_node_label(node))}]",
                        )
                    )
                lines.append("        end")
            lines.append("    end")
        lines.append("end")


def _render_dot_ownership(lines: list[str], projection: V2TopologyProjection) -> None:
    for host_id, services in _ownership_tree(projection).items():
        lines.extend(
            (
                f'    subgraph "cluster_{_identifier("host", host_id)}" {{',
                f"        graph [id={_dot_string(host_id)}, label={_dot_string(f'host {host_id}')}];",
            )
        )
        for service_id, components in services.items():
            service_ref = f"{host_id}/{service_id}"
            lines.extend(
                (
                    f'        subgraph "cluster_{_identifier("service", service_ref)}" {{',
                    f"            graph [id={_dot_string(service_ref)}, label={_dot_string(f'service {service_id}')}];",
                )
            )
            for component_id, nodes in components.items():
                component_ref = f"{service_ref}/{component_id}"
                lines.extend(
                    (
                        f'            subgraph "cluster_{_identifier("component", component_ref)}" {{',
                        f"                graph [id={_dot_string(component_ref)}, label={_dot_string(f'component {component_id}')}];",
                    )
                )
                for node in nodes:
                    lines.append(
                        f'                "{_endpoint_identifier(node.id)}" '
                        f"[id={_dot_string(node.id)}, label={_dot_string(_node_label(node))}];"
                    )
                lines.append("            }")
            lines.append("        }")
        lines.append("    }")


OwnershipTree = dict[str, dict[str, dict[str, list[V2TopologyNode]]]]


def _ownership_tree(projection: V2TopologyProjection) -> OwnershipTree:
    tree: defaultdict[str, defaultdict[str, defaultdict[str, list[V2TopologyNode]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for owner in sorted(
        projection.components,
        key=lambda current: (
            current.host_id,
            current.service_instance_id,
            current.component_slot_id,
        ),
    ):
        tree[owner.host_id][owner.service_instance_id][owner.component_slot_id]
    for node in sorted(
        projection.nodes,
        key=lambda current: (
            current.owner.host_id,
            current.owner.service_instance_id,
            current.owner.component_slot_id,
            current.id,
        ),
    ):
        tree[node.owner.host_id][node.owner.service_instance_id][
            node.owner.component_slot_id
        ].append(node)
    return {
        host_id: {
            service_id: {
                component_id: list(nodes) for component_id, nodes in sorted(components.items())
            }
            for service_id, components in sorted(services.items())
        }
        for host_id, services in sorted(tree.items())
    }


def _finalize_render(lines: list[str]) -> str:
    rendered = "\n".join(lines) + "\n"
    if len(rendered.encode("utf-8")) > _MAX_RENDERED_BYTES:
        raise V2TopologyRenderBoundsError(
            f"rendered topology exceeds {_MAX_RENDERED_BYTES}-byte limit"
        )
    return rendered


def _identifier(kind: Literal["host", "service", "component"], canonical_id: str) -> str:
    return f"{kind}_{_digest(canonical_id)}"


def _endpoint_identifier(canonical_id: str) -> str:
    return f"endpoint_{_digest(canonical_id)}"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _node_label(node: V2TopologyNode) -> str:
    return f"{node.endpoint_id} ({node.protocol.value}:{node.port})"


def _edge_label(edge_id: str, scope: str) -> str:
    return f"{edge_id} ({scope})"


def _metadata(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _mermaid_label(value: str) -> str:
    escapes = {
        "&": "&amp;",
        '"': "&quot;",
        "'": "&#39;",
        "<": "&lt;",
        ">": "&gt;",
        "[": "&#91;",
        "]": "&#93;",
        "{": "&#123;",
        "}": "&#125;",
        "|": "&#124;",
        "\\": "&#92;",
        "\n": "&#10;",
        "\r": "&#13;",
    }
    return '"' + "".join(escapes.get(character, character) for character in value) + '"'


def _dot_string(value: str) -> str:
    return _metadata(value)
