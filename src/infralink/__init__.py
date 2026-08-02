"""
Infralink - Infrastructure topology modeling with UUID-based nodes and typed edges.

This package provides tools for:
- Declaring infrastructure nodes with UUID primary keys
- Defining typed edges between nodes (database, queue, cluster, telemetry, etc.)
- Resolving edge targets for template rendering
- Health checking edge connectivity
- Generating infrastructure diagrams (Mermaid, D2, Graphviz)
- Generating documentation from topology declarations
"""

from infralink.__about__ import __version__
from infralink.core.edges import Edge, EdgeSet
from infralink.core.registry import Registry
from infralink.core.resolver import EdgeResolver
from infralink.secrets import SecretAudit, SecretReference, SecretResolver, SecretValue

__all__ = [
    "__version__",
    "Registry",
    "EdgeSet",
    "Edge",
    "EdgeResolver",
    "SecretAudit",
    "SecretReference",
    "SecretResolver",
    "SecretValue",
]
