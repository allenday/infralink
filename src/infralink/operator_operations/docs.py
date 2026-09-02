"""Generate bounded documentation artifacts from a Registry checkout."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Literal

from agent_surface import OperationError
from pydantic import Field, StrictInt

from infralink.cli.artifacts import (
    artifact_fingerprint,
    artifact_metadata,
    artifact_pages,
    artifact_usage,
    require_output,
    write_artifacts,
)
from infralink.cli.contracts import ArtifactResult, ArtifactSummary
from infralink.operator_sources import SourceRequest, load_sources


class DocsRequest(SourceRequest):
    """Write selected documentation artifacts from one Registry checkout."""

    output: Path
    document_format: Literal["markdown", "html"] = Field(
        default="markdown",
        json_schema_extra={"cli": {"options": ["--document-format"]}},
    )
    host: str | None = Field(default=None, min_length=1)
    index_only: bool = False
    limit: StrictInt = Field(default=20, ge=1, le=1000)
    cursor: str | None = None
    collection: str | None = None


def _document_bytes(markdown: str, output_format: str) -> tuple[str, bytes]:
    if output_format == "markdown":
        return "text/markdown", markdown.encode("utf-8")
    document = (
        '<!doctype html>\n<html><head><meta charset="utf-8"></head>'
        f"<body><pre>{html.escape(markdown)}</pre></body></html>\n"
    )
    return "text/html", document.encode("utf-8")


def generate_declared_docs(request: DocsRequest) -> ArtifactResult:
    """Write documentation from declared topology without deployment effects."""
    output = require_output(request.output)
    sources = load_sources(request)
    from infralink.generators.markdown import (
        generate_edge_index,
        generate_host_doc,
        generate_index,
    )

    registry = sources.registry
    edges = sources.edges
    extension = "md" if request.document_format == "markdown" else "html"
    generated: list[tuple[Path, str, bytes]] = []
    media_type, body = _document_bytes(generate_index(registry, edges), request.document_format)
    generated.append((Path(f"index.{extension}"), media_type, body))

    if not request.index_only:
        hosts = registry.active_hosts()
        if request.host:
            host = registry.get(request.host)
            if host is None:
                raise OperationError(
                    "entity_not_found",
                    message="Host was not found",
                    fix="Run infralink host list.",
                    details=({"entity_type": "host", "requested_id": request.host},),
                )
            hosts = [host]
        for host in sorted(hosts, key=lambda item: item.canonical_name):
            if Path(host.canonical_name).name != host.canonical_name:
                raise artifact_usage("Host name cannot be used as a safe artifact path")
            media_type, body = _document_bytes(
                generate_host_doc(host, edges, registry),
                request.document_format,
            )
            generated.append((Path(f"{host.canonical_name}.{extension}"), media_type, body))
        if len(edges) > 0:
            media_type, body = _document_bytes(
                generate_edge_index(edges, registry),
                request.document_format,
            )
            generated.append((Path("edges") / f"index.{extension}", media_type, body))

    artifacts = artifact_metadata(output, generated)
    selected = request.collection or "artifacts"
    fingerprint = artifact_fingerprint(
        command="docs",
        sources=[sources.registry_path, sources.edges_path],
        options={
            "document_format": request.document_format,
            "host": request.host,
            "index_only": request.index_only,
            "output": output.as_posix(),
        },
        collections={"artifacts": artifacts},
    )
    pages = artifact_pages(
        command="docs",
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
