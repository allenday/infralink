"""Generate bounded legacy topology diagram artifacts from a Registry checkout."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt

from infralink.cli.artifacts import (
    artifact_fingerprint,
    artifact_metadata,
    artifact_pages,
    require_output,
    write_artifacts,
)
from infralink.cli.contracts import ArtifactResult, ArtifactSummary
from infralink.operator_sources import SourceRequest, load_sources


class DiagramRequest(SourceRequest):
    """Write selected topology diagram artifacts from one Registry checkout."""

    output: Path
    diagram_format: Literal["mermaid", "d2", "dot", "all"] = Field(
        default="mermaid",
        json_schema_extra={"cli": {"options": ["--diagram-format"]}},
    )
    group: str | None = Field(default=None, min_length=1)
    include_terminated: bool = False
    limit: StrictInt = Field(default=20, ge=1, le=1000)
    cursor: str | None = None
    collection: str | None = None


def generate_declared_diagrams(request: DiagramRequest) -> ArtifactResult:
    """Write legacy topology diagrams from declared sources without deployment effects."""
    output = require_output(request.output)
    sources = load_sources(request)
    from infralink.generators.d2 import generate_d2
    from infralink.generators.dot import generate_dot
    from infralink.generators.mermaid import generate_mermaid

    registry = sources.registry
    edges = sources.edges
    if request.group:
        hosts = [host for host in registry if host.group == request.group]
    elif request.include_terminated:
        hosts = list(registry)
    else:
        hosts = registry.active_hosts()

    generators = {
        "mermaid": (generate_mermaid, Path("infrastructure.md"), "text/markdown"),
        "d2": (generate_d2, Path("infrastructure.d2"), "text/vnd.d2"),
        "dot": (generate_dot, Path("infrastructure.dot"), "text/vnd.graphviz"),
    }
    formats = tuple(generators) if request.diagram_format == "all" else (request.diagram_format,)
    generated = [
        (filename, media_type, generator(hosts, edges, registry).encode("utf-8"))
        for name in formats
        for generator, filename, media_type in [generators[name]]
    ]
    artifacts = artifact_metadata(output, generated)
    selected = request.collection or "artifacts"
    fingerprint = artifact_fingerprint(
        command="diagram",
        sources=[sources.registry_path, sources.edges_path],
        options={
            "diagram_format": request.diagram_format,
            "output": output.as_posix(),
            "group": request.group,
            "include_terminated": request.include_terminated,
        },
        collections={"artifacts": artifacts},
    )
    pages = artifact_pages(
        command="diagram",
        collections={"artifacts": artifacts},
        selected=selected,
        cursor=request.cursor,
        limit=request.limit,
        fingerprint=fingerprint,
    )
    write_artifacts(output, generated)
    return ArtifactResult(
        artifacts=pages["artifacts"],
        summary=ArtifactSummary(artifact_count=len(artifacts)),
    )
