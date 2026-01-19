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

__version__ = "0.1.0"

from infralink.core.registry import Registry
from infralink.core.edges import EdgeSet, Edge
from infralink.core.resolver import EdgeResolver

__all__ = [
    "__version__",
    "Registry",
    "EdgeSet",
    "Edge",
    "EdgeResolver",
]
