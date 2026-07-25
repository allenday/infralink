"""Metadata-only secret inventory and provider audit commands."""

from __future__ import annotations

import importlib
import os
import shlex
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import click

from infralink.cli.actions import action
from infralink.cli.contracts import (
    SecretReferenceStatus,
    SecretsAuditResult,
    SecretsInspectResult,
    SecretsSummary,
    SourceLocation,
)
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.main import (
    Context,
    _active_collection,
    _attach_next_cursors,
    _emit_query_result,
    _page_offset,
    _page_options,
    _root_source_argv,
    _topology_fingerprint,
    pass_context,
)
from infralink.cli.pagination import page_items
from infralink.secrets import SecretAudit, SecretReference
from infralink.secrets.inventory import collect_secret_references

_COLLECTIONS = ("references", "locations")
_PROVIDER_MESSAGES = {
    ErrorCode.PROVIDER_UNAVAILABLE: "Secret provider is unavailable",
    ErrorCode.PROVIDER_AUTHENTICATION_FAILED: "Secret provider authentication failed",
    ErrorCode.PROVIDER_AUTHORIZATION_FAILED: "Secret provider authorization failed",
    ErrorCode.PROVIDER_TIMEOUT: "Secret provider timed out",
}


def _build_bws_resolver() -> Any:
    """Build the hosted BWS adapter without importing it for offline commands."""
    importlib.import_module("bitwarden_sdk")
    from infralink.adapters.bws import BwsConfig, BwsSecretResolver

    return BwsSecretResolver(config=BwsConfig.from_env())


def _locations(reference: SecretReference) -> list[SourceLocation]:
    return [SourceLocation(source="edges", path=path) for path in reference.locations]


def _status(
    reference: SecretReference,
    audit: SecretAudit | None = None,
    *,
    include_preview: bool,
) -> SecretReferenceStatus:
    locations = _locations(reference)
    return SecretReferenceStatus(
        ref=reference.ref,
        location_count=len(locations),
        location_preview=locations[:16] if include_preview else [],
        locations_truncated=include_preview and len(locations) > 16,
        project=reference.project if audit is None else audit.project,
        present=None if audit is None else audit.present,
        accessible=None if audit is None else audit.accessible,
        error_code=None if audit is None else audit.error_code,
    )


def _summary(statuses: Sequence[SecretReferenceStatus]) -> SecretsSummary:
    return SecretsSummary(
        total=len(statuses),
        present=sum(item.present is True for item in statuses),
        missing=sum(
            item.present is False or item.error_code == "unavailable_or_missing"
            for item in statuses
        ),
        accessible=sum(item.accessible is True for item in statuses),
        denied=sum(item.accessible is False for item in statuses),
    )


def _select_references(
    ctx: Context,
    requested_ref: str | None,
) -> list[SecretReference]:
    references = collect_secret_references(ctx.registry, ctx.edges)
    if requested_ref is None:
        return references
    selected = [reference for reference in references if reference.ref == requested_ref]
    if selected:
        return selected
    discovery = [*_root_source_argv(ctx), "secrets", "inspect"]
    raise CliFailure(
        code=ErrorCode.ENTITY_NOT_FOUND,
        message="Secret reference not found",
        exit_code=3,
        fix=f"Run {shlex.join(discovery)}",
        details={
            "entity_type": "secret_reference",
            "requested_id": requested_ref,
        },
        next_actions=[action("inspect", discovery, "Inspect declared secret references")],
    )


def _fingerprint(
    ctx: Context,
    *,
    requested_ref: str | None,
    provider: str | None = None,
) -> str:
    identifiers = {"ref": requested_ref or ""}
    if provider is not None:
        identifiers["provider"] = provider
    return _topology_fingerprint(
        ctx,
        include_registry=True,
        include_edges=True,
        identifiers=identifiers,
    )


def _provider_failure(code: ErrorCode, *, missing_sdk: bool = False) -> CliFailure:
    actions = []
    fix = "Verify hosted provider configuration and retry"
    if missing_sdk:
        fix = "Install the optional BWS dependency"
        actions.append(
            action(
                "install",
                ["python", "-m", "pip", "install", "infralink[bws]"],
                "Install the optional BWS dependency",
                safe=False,
            )
        )
    return CliFailure(
        code=code,
        message=_PROVIDER_MESSAGES[code],
        exit_code=4,
        fix=fix,
        next_actions=actions,
    )


def _audit_with_bws(references: list[SecretReference]) -> list[SecretAudit]:
    try:
        canonical_references = [
            SecretReference(
                ref=reference.ref,
                project=str(UUID(reference.project)) if reference.project is not None else None,
                locations=reference.locations,
                required=reference.required,
            )
            for reference in references
        ]
    except (AttributeError, TypeError, ValueError):
        raise _provider_failure(ErrorCode.PROVIDER_UNAVAILABLE) from None
    if any(reference.project is None for reference in canonical_references):
        raise _provider_failure(ErrorCode.PROVIDER_UNAVAILABLE)

    try:
        resolver = _build_bws_resolver()
        audits = resolver.audit(canonical_references)
    except ModuleNotFoundError:
        raise _provider_failure(ErrorCode.PROVIDER_UNAVAILABLE, missing_sdk=True) from None
    except Exception as exc:
        from infralink.adapters.bws import (
            BwsConfigurationError,
            BwsErrorCode,
            BwsProviderError,
        )

        if isinstance(exc, BwsConfigurationError):
            missing_identity = not os.environ.get("BWS_ACCESS_TOKEN") or not os.environ.get(
                "BWS_ORGANIZATION_ID"
            )
            code = (
                ErrorCode.PROVIDER_AUTHENTICATION_FAILED
                if missing_identity
                else ErrorCode.PROVIDER_UNAVAILABLE
            )
            raise _provider_failure(code) from None
        if isinstance(exc, BwsProviderError):
            mapping = {
                BwsErrorCode.PROVIDER_UNAVAILABLE: ErrorCode.PROVIDER_UNAVAILABLE,
                BwsErrorCode.PROVIDER_AUTHENTICATION_FAILED: (
                    ErrorCode.PROVIDER_AUTHENTICATION_FAILED
                ),
                BwsErrorCode.PROVIDER_AUTHORIZATION_FAILED: (
                    ErrorCode.PROVIDER_AUTHORIZATION_FAILED
                ),
                BwsErrorCode.PROVIDER_TIMEOUT: ErrorCode.PROVIDER_TIMEOUT,
            }
            raise _provider_failure(mapping[exc.code]) from None
        raise

    expected = {(item.ref, item.project) for item in canonical_references}
    indexed: dict[tuple[str, str | None], SecretAudit] = {}
    for audit in audits:
        identity = (audit.ref, audit.project)
        if identity not in expected or identity in indexed:
            raise _provider_failure(ErrorCode.PROVIDER_UNAVAILABLE)
        indexed[identity] = audit
    if len(indexed) != len(canonical_references):
        raise _provider_failure(ErrorCode.PROVIDER_UNAVAILABLE)
    return [indexed[(item.ref, item.project)] for item in canonical_references]


@click.group()
def secrets() -> None:
    """Inspect and audit secret-reference metadata."""


@secrets.command(name="inspect")
@click.option("--ref", "requested_ref", type=str, default=None)
@_page_options
@pass_context
def inspect_secrets(
    ctx: Context,
    requested_ref: str | None,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Inspect declared secret references without provider access."""
    selected_collection = _active_collection(collection, cursor, _COLLECTIONS)
    fingerprint = _fingerprint(ctx, requested_ref=requested_ref)
    offset = _page_offset(
        command="secrets inspect",
        collection=selected_collection,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    references = _select_references(ctx, requested_ref)
    statuses = [_status(item, include_preview=True) for item in references]
    all_locations = (
        sorted(
            {
                (location.source, location.path): location
                for item in references
                for location in _locations(item)
            }.values(),
            key=lambda item: (item.source, item.path),
        )
        if requested_ref is not None
        else []
    )
    result = SecretsInspectResult(
        references=page_items(
            statuses,
            limit=limit,
            offset=offset if selected_collection == "references" else 0,
            next_cursor=None,
        ),
        locations=page_items(
            all_locations,
            limit=limit,
            offset=offset if selected_collection == "locations" else 0,
            next_cursor=None,
        ),
        summary=_summary(statuses),
    )
    _attach_next_cursors(
        result,
        command="secrets inspect",
        collections=_COLLECTIONS,
        selected=selected_collection,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    command_argv = ["secrets", "inspect"]
    if requested_ref is not None:
        command_argv.extend(["--ref", requested_ref])
    truncated_refs = sorted({item.ref for item in statuses if item.locations_truncated})
    escalation_actions = [
        action(
            "inspect",
            [
                *_root_source_argv(ctx),
                "secrets",
                "inspect",
                "--ref",
                ref,
                "--collection",
                "locations",
            ],
            "Inspect all declaration locations",
        )
        for ref in truncated_refs
    ]
    _emit_query_result(
        ctx=ctx,
        path=["secrets", "inspect"],
        command_argv=command_argv,
        result=result,
        limit=limit,
        extra_actions=escalation_actions,
        content_truncated=bool(truncated_refs),
    )


@secrets.command(name="audit")
@click.option(
    "--provider",
    type=click.Choice(["bws"], case_sensitive=False),
    default="bws",
    show_default=True,
)
@click.option("--ref", "requested_ref", type=str, default=None)
@_page_options
@pass_context
def audit_secrets(
    ctx: Context,
    provider: str,
    requested_ref: str | None,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Audit declared secret references using provider metadata only."""
    provider = provider.casefold()
    selected_collection = _active_collection(collection, cursor, ("references",))
    fingerprint = _fingerprint(ctx, requested_ref=requested_ref, provider=provider)
    offset = _page_offset(
        command="secrets audit",
        collection=selected_collection,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    references = _select_references(ctx, requested_ref)
    audits = _audit_with_bws(references) if references else []
    statuses = [
        _status(reference, audit, include_preview=True)
        for reference, audit in zip(references, audits, strict=True)
    ]
    result = SecretsAuditResult(
        provider=provider,
        references=page_items(
            statuses,
            limit=limit,
            offset=offset,
            next_cursor=None,
        ),
        summary=_summary(statuses),
    )
    _attach_next_cursors(
        result,
        command="secrets audit",
        collections=("references",),
        selected=selected_collection,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    command_argv = ["secrets", "audit", "--provider", provider]
    if requested_ref is not None:
        command_argv.extend(["--ref", requested_ref])
    truncated_refs = sorted({item.ref for item in statuses if item.locations_truncated})
    escalation_actions = [
        action(
            "inspect",
            [
                *_root_source_argv(ctx),
                "secrets",
                "inspect",
                "--ref",
                ref,
                "--collection",
                "locations",
            ],
            "Inspect all declaration locations",
        )
        for ref in truncated_refs
    ]
    _emit_query_result(
        ctx=ctx,
        path=["secrets", "audit"],
        command_argv=command_argv,
        result=result,
        limit=limit,
        extra_actions=escalation_actions,
        resolved={"provider": provider},
        content_truncated=bool(truncated_refs),
    )
    if any(item.present is not True or item.accessible is not True for item in statuses):
        raise SystemExit(1)
