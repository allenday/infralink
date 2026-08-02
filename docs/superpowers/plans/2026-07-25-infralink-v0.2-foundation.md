# Infralink v0.2 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a tagged-ready Infralink `v0.2.0` candidate with stable read-only Python and JSON CLI contracts, an optional hosted Bitwarden Secrets Manager adapter, and public/private artifact verification.

**Architecture:** Harden the existing package in place. Domain and application code remain provider-neutral; CLI commands serialize typed application results through one versioned envelope; the optional BWS adapter implements a read-only secret resolver behind opaque values. GitHub builds the public candidate once, and a non-deploying Woodpecker job verifies those exact bytes against the private registry before release.

**Tech Stack:** Python 3.10-3.12, Pydantic v2, Click, Hatchling, pytest, pytest-cov, Ruff, mypy, JSON Schema, Bitwarden SDK 2.x, GitHub Actions, Woodpecker CI.

---

## Working Context

Run Tasks 1-12 and 14 from the Infralink worktree:

```bash
cd /root/src/infra-management-ci-validate/third-party/infralink
git status --short --branch
```

Expected branch at plan handoff:

```text
## docs/infralink-v0.2-foundation
```

Create the implementation branch from the approved design commit:

```bash
git switch -c feat/infralink-v0.2-foundation
```

Bootstrap the named development environment before Task 1:

```bash
python3.12 -m venv /tmp/infralink-plan-venv
/tmp/infralink-plan-venv/bin/pip install --upgrade pip
/tmp/infralink-plan-venv/bin/pip install \
  'build>=1.2' 'bitwarden-sdk>=2.1,<3' 'jsonschema>=4.23' \
  'mypy>=1.0' 'pytest>=7.0' 'pytest-cov>=4.0' 'ruff>=0.1.0' \
  'twine>=5.1' types-PyYAML
/tmp/infralink-plan-venv/bin/pip install -e .
/tmp/infralink-plan-venv/bin/python -c \
  "import build, jsonschema, pytest, twine; import bitwarden_sdk"
```

Task 2 adds any dependency entries missing from the current `dev` extra. Re-run
the editable install after Task 2 so later commands use the committed metadata.

Run Task 13 in a clean `infra-management` worktree based on current `main`.
Do not update a production host, run `self-deploy.sh`, restart a service, or
create a release tag while executing this plan.

Current baseline on Python 3.12:

```text
59 tests: 50 passed, 9 failed
coverage: 49%
ruff: 46 findings
mypy: 42 errors
```

## File Map

### Infralink Files To Create

- `src/infralink/__about__.py`: single package version source.
- `src/infralink/cli/contracts.py`: typed CLI envelope and result contracts.
- `src/infralink/cli/actions.py`: structured HATEOAS action builders.
- `src/infralink/cli/errors.py`: stable public error codes and exit mapping.
- `src/infralink/cli/pagination.py`: bounded pages and opaque cursors.
- `src/infralink/cli/queries.py`: read-only host, service, edge, and app serializers.
- `src/infralink/cli/secrets.py`: `secrets inspect` and `secrets audit` commands.
- `src/infralink/secrets/__init__.py`: public secret abstractions.
- `src/infralink/secrets/base.py`: `SecretValue`, references, audit results, and resolver protocol.
- `src/infralink/secrets/inventory.py`: derive declared secret references from topology.
- `src/infralink/adapters/__init__.py`: optional provider package.
- `src/infralink/adapters/bws.py`: hosted-only BWS SDK adapter and fake test
  configuration.
- `src/infralink/schemas/cli/v1/*.json`: generated normative command schemas.
- `scripts/generate_cli_schemas.py`: deterministic schema generation.
- `scripts/build_release_manifest.py`: candidate provenance manifest.
- `tests/cli_helpers.py`: JSON invocation and schema assertions.
- `tests/test_cli_contracts.py`: envelope, action, and schema tests.
- `tests/test_cli_discovery.py`: root/help/version and exception conversion.
- `tests/test_cli_pagination.py`: cursor and bounded-collection behavior.
- `tests/test_cli_queries.py`: list/detail result contracts.
- `tests/test_secret_value.py`: opaque secret guarantees.
- `tests/test_secret_inventory.py`: declared-reference extraction.
- `tests/test_bws_adapter.py`: fake-SDK adapter and security tests.
- `tests/test_cli_secrets.py`: metadata-only secret commands.
- `tests/test_release_manifest.py`: build-once provenance behavior.
- `.github/workflows/release-candidate.yml`: build and attest candidate bytes.
- `.github/workflows/release.yml`: promote verified bytes without rebuilding.
- `docs/compatibility/v0.2.md`: consumer inventory and migration evidence.
- `docs/releases/v0.2.0.md`: operator-facing release and rollback notes.

### Infralink Files To Modify

- `pyproject.toml`: dynamic version, optional BWS extra, build/test tooling, package data.
- `src/infralink/__init__.py`: import the single version source and new public abstractions.
- `src/infralink/cli/output.py`: one serializer for success and error envelopes.
- `src/infralink/cli/main.py`: JSON-only group, discovery, shared options, and detail commands.
- `src/infralink/cli/{validate,check,resolve,app,analyze,diagram,docs}.py`: typed results and stable exits.
- `src/infralink/core/{registry,resolver,schema,edges}.py`: compatibility repairs and safe resolution.
- `src/infralink/health/checks.py`: typed health results.
- `src/infralink/generators/{mermaid,d2,dot,markdown}.py`: type and summary cleanup.
- `tests/test_{registry,resolver,cli_output,cli_root,cli_json,cli_commands_json,cli_validate}.py`: baseline and compatibility coverage.
- `.github/workflows/ci.yml`: full quality and wheel-install matrix.
- `README.md`, `PRD.md`, `BACKLOG.md`: safe CLI examples and release status.

### Infra-Management Files To Create Or Modify

- Create `scripts/verify_infralink_candidate.py`: verify manifest and install exact wheel.
- Create `tests/test_infralink_candidate_contract.py`: run recorded private consumer workflows.
- Modify `.woodpecker.yml`: add a parameter-discriminated, non-deploying
  candidate gate and exclude deployment steps from that mode.
- Create `tests/test_woodpecker_infralink_policy.py`: prove the candidate
  pipeline cannot select deployment steps.
- Modify `third-party/infralink`: only after the candidate passes and `v0.2.0` exists.

---

### Task 1: Restore A Green Behavioral Baseline

**Files:**
- Modify: `src/infralink/core/registry.py`
- Modify: `tests/test_registry.py`
- Modify: `tests/test_resolver.py`
- Test: `tests/test_cli_commands_json.py`
- Test: `tests/test_cli_json.py`

- [ ] **Step 1: Add a failing compatibility test for legacy `group` reads**

Add to `tests/test_registry.py`:

```python
def test_group_compatibility_uses_first_project() -> None:
    host = Host(
        "d1b9e5d5-36b0-459d-a556-96622811fbd5",
        {
            "canonical_name": "test-host",
            "status": "active",
            "projects": ["production", "shared"],
        },
    )

    assert host.group == "production"
```

- [ ] **Step 2: Run the focused test and verify the current failure**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_registry.py::test_group_compatibility_uses_first_project -q
```

Expected: `AttributeError: 'Host' object has no attribute 'group'`.

- [ ] **Step 3: Restore the read-only compatibility property**

Add beside `Host.projects` in `src/infralink/core/registry.py`:

```python
    @property
    def group(self) -> str | None:
        """Legacy single-project view retained for pre-v0.2 consumers."""
        return self.projects[0] if self.projects else None
```

Remove the duplicate `result = self._schema.model_dump(by_alias=True)` line in
`Host.to_dict()`.

- [ ] **Step 4: Make resolver tests assert the current two-list contract**

Change the two `validate_all` tests in `tests/test_resolver.py` to:

```python
    def test_validate_all(self, registry, edges):
        resolver = EdgeResolver(registry, edges)

        errors, warnings = resolver.validate_all()

        assert errors == []
        assert len(warnings) == 2
        assert all("missing an explicit healthcheck" in warning for warning in warnings)

    def test_validate_all_with_missing_target(self, registry):
        edges = EdgeSet.from_dict(
            {
                "edges": [
                    {
                        "id": "8d11e0b6-14b0-4f12-a6ed-5a76a8a0dbf2",
                        "type": "database",
                        "from": {"hosts": [], "service": "app"},
                        "to": {
                            "host": "nonexistent-uuid",
                            "service": "postgresql",
                            "port": 5432,
                        },
                        "healthcheck": {"type": "tcp"},
                    }
                ]
            }
        )
        resolver = EdgeResolver(registry, edges)

        errors, warnings = resolver.validate_all()

        assert len(errors) == 1
        assert "not found" in errors[0]
        assert warnings == []
```

- [ ] **Step 5: Run the complete baseline suite**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest -q
```

Expected: `59 passed`; coverage remains near the existing baseline.

- [ ] **Step 6: Commit the baseline repair**

```bash
git add src/infralink/core/registry.py tests/test_registry.py tests/test_resolver.py
git commit -m "fix: restore infralink compatibility baseline"
```

---

### Task 2: Establish One Version And Installable Extras

**Files:**
- Create: `src/infralink/__about__.py`
- Modify: `src/infralink/__init__.py`
- Modify: `pyproject.toml`
- Create: `tests/test_package_metadata.py`

- [ ] **Step 1: Write failing tests for version and optional dependency metadata**

Create `tests/test_package_metadata.py`:

```python
from pathlib import Path

import tomllib

import infralink


ROOT = Path(__file__).parents[1]


def test_package_reports_v020() -> None:
    assert infralink.__version__ == "0.2.0"


def test_pyproject_declares_bws_and_release_tooling() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["dynamic"] == ["version"]
    assert data["project"]["optional-dependencies"]["bws"] == [
        "bitwarden-sdk>=2.1,<3"
    ]
    dev = data["project"]["optional-dependencies"]["dev"]
    assert "build>=1.2" in dev
    assert "twine>=5.1" in dev
    assert "jsonschema>=4.23" in dev
    assert data["project"]["urls"] == {
        "Homepage": "https://github.com/cyberstorm-dev/infralink",
        "Issues": "https://github.com/cyberstorm-dev/infralink/issues",
        "Source": "https://github.com/cyberstorm-dev/infralink",
    }
```

- [ ] **Step 2: Verify the tests fail on `0.1.0`**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest tests/test_package_metadata.py -q
```

Expected: failures for version, dynamic metadata, and missing `bws`.

- [ ] **Step 3: Add the single version source**

Create `src/infralink/__about__.py`:

```python
__version__ = "0.2.0"
```

Replace the literal version in `src/infralink/__init__.py` with:

```python
from infralink.__about__ import __version__
from infralink.core.edges import Edge, EdgeSet
from infralink.core.registry import Registry
from infralink.core.resolver import EdgeResolver

__all__ = ["__version__", "Registry", "EdgeSet", "Edge", "EdgeResolver"]
```

- [ ] **Step 4: Configure Hatchling and optional dependencies**

In `pyproject.toml`, replace `version = "0.1.0"` with:

```toml
dynamic = ["version"]
```

Add:

```toml
[project.optional-dependencies]
bws = [
    "bitwarden-sdk>=2.1,<3",
]
dev = [
    "build>=1.2",
    "jsonschema>=4.23",
    "mypy>=1.0",
    "pytest>=7.0",
    "pytest-cov>=4.0",
    "ruff>=0.1.0",
    "twine>=5.1",
    "types-PyYAML",
]

[tool.hatch.version]
path = "src/infralink/__about__.py"

[tool.hatch.build.targets.wheel]
packages = ["src/infralink"]
```

Keep the existing `docs` extra and remove the old duplicate `dev` table rather
than defining the same TOML key twice. Replace every
`https://github.com/example/infralink` project URL with the exact
`https://github.com/cyberstorm-dev/infralink` URLs asserted above. Schema
package-data configuration is added in Task 3 after the directory exists.

- [ ] **Step 5: Run metadata tests and build metadata inspection**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest tests/test_package_metadata.py -q
/tmp/infralink-plan-venv/bin/pip install -e '.[dev,bws]'
/tmp/infralink-plan-venv/bin/python -m build
/tmp/infralink-plan-venv/bin/python -m twine check dist/*
```

Expected: tests pass; wheel and sdist are named `infralink-0.2.0`; Twine reports
`PASSED`.

- [ ] **Step 6: Commit version and packaging metadata**

```bash
git add pyproject.toml src/infralink/__about__.py src/infralink/__init__.py tests/test_package_metadata.py
git commit -m "build: prepare infralink 0.2 package metadata"
```

---

### Task 3: Add Typed CLI Contracts And Generated Schemas

**Files:**
- Create: `src/infralink/cli/contracts.py`
- Create: `scripts/generate_cli_schemas.py`
- Create: `tests/test_cli_contracts.py`
- Create: `src/infralink/schemas/cli/v1/`

- [ ] **Step 1: Write failing shared-contract tests**

Create `tests/test_cli_contracts.py` with:

```python
from pathlib import Path

from jsonschema import Draft202012Validator

from infralink.cli.contracts import (
    Action,
    CommandContext,
    Diagnostic,
    Envelope,
    Page,
    PageInfo,
    ValidateResult,
)


ROOT = Path(__file__).parents[1]


def test_validate_envelope_is_typed_and_serializable() -> None:
    payload = Envelope[ValidateResult](
        ok=True,
        command=CommandContext(
            raw="infralink validate",
            parsed={"path": ["validate"], "args": {}, "flags": []},
            resolved={"version": "0.2.0", "cwd": "/work"},
        ),
        result=ValidateResult(
            valid=True,
            errors=Page(items=[], page=PageInfo(limit=100, returned=0, total=0)),
            warnings=Page(items=[], page=PageInfo(limit=100, returned=0, total=0)),
            summary={"error_count": 0, "warning_count": 0},
        ),
        next_actions=[
            Action(
                rel="check",
                argv=["infralink", "check"],
                command="infralink check",
                description="Check declared edge health",
                safe=True,
            )
        ],
    ).model_dump(mode="json", exclude_none=True)

    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["result"]["valid"] is True


def test_generated_validate_schema_is_valid_draft_2020_12() -> None:
    schema_path = ROOT / "src/infralink/schemas/cli/v1/validate.json"
    Draft202012Validator.check_schema(
        __import__("json").loads(schema_path.read_text(encoding="utf-8"))
    )


def test_diagnostic_severity_is_closed() -> None:
    diagnostic = Diagnostic(code="bad_edge", message="Bad edge", severity="error")
    assert diagnostic.severity == "error"
```

- [ ] **Step 2: Run the tests and verify missing contracts**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest tests/test_cli_contracts.py -q
```

Expected: import failure for `infralink.cli.contracts`.

- [ ] **Step 3: Create the shared Pydantic contracts**

Create `src/infralink/cli/contracts.py`. Define all models named in the approved
spec. The core shared implementation must include:

```python
from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic import model_validator


T = TypeVar("T")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageInfo(ContractModel):
    limit: int = Field(ge=1, le=1000)
    returned: int = Field(ge=0)
    total: int | None = Field(default=None, ge=0)
    next_cursor: str | None = None


class Page(ContractModel, Generic[T]):
    items: list[T]
    page: PageInfo


class Binding(ContractModel):
    type: Literal["string", "integer", "boolean"]
    required: bool
    source: str


class Action(ContractModel):
    rel: str
    argv: list[str]
    command: str
    description: str
    safe: bool
    templated: bool = False
    bindings: dict[str, Binding] = Field(default_factory=dict)


class CommandContext(ContractModel):
    raw: str
    parsed: dict[str, Any]
    resolved: dict[str, Any]


class ErrorDetail(ContractModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Meta(ContractModel):
    truncated: bool = False


class Envelope(ContractModel, Generic[T]):
    schema_version: Literal["infralink.cli/v1"] = "infralink.cli/v1"
    ok: bool
    command: CommandContext
    result: T | None = None
    error: ErrorDetail | None = None
    fix: str | None = None
    next_actions: list[Action]
    meta: Meta = Field(default_factory=Meta)

    @model_validator(mode="after")
    def enforce_outcome(self) -> Envelope[T]:
        success = self.ok and self.result is not None and self.error is None
        failure = not self.ok and self.result is None and self.error is not None
        if not (success or failure):
            raise ValueError("ok must select exactly one of result or error")
        return self


class Diagnostic(ContractModel):
    code: str
    path: str | None = None
    message: str
    severity: Literal["error", "warning"]


class ValidateResult(ContractModel):
    valid: bool
    errors: Page[Diagnostic]
    warnings: Page[Diagnostic]
    summary: dict[str, int]
```

In the same file, add the remaining approved shared types and result models:
`HostSummary`, `EdgeSummary`, `Endpoint`, `CheckResult`, `AppSummary`,
`ServiceSummary`, `SourceLocation`, `SecretReferenceStatus`, `Artifact`,
`RootResult`, `HelpResult`, `VersionResult`, `InfoResult`, `HostListResult`,
`HostShowResult`, `ServiceListResult`, `ServiceShowResult`, `EdgeListResult`,
`EdgeShowResult`, `ResolveResult`, `CheckCommandResult`, `AppListResult`,
`AppShowResult`, `AnalyzeResult`, `ArtifactResult`, `SecretsInspectResult`, and
`SecretsAuditResult`. Use the field types and `max_length` bounds from the
approved design exactly.

Add negative tests that reject both/neither `result` and `error`, a result with
`ok: false`, and an error with `ok: true`. In `generate_cli_schemas.py`, add a
top-level JSON Schema `oneOf`: the success branch requires `ok: true` and
`result` while forbidding `error`; the failure branch requires `ok: false` and
`error` while forbidding `result`. Validate the four invalid fixture documents
against every generated command schema. This keeps the invariant normative in
both runtime Python validation and the published schemas.

- [ ] **Step 4: Generate one schema per command deterministically**

Create `scripts/generate_cli_schemas.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infralink.cli.contracts import (
    AnalyzeResult,
    AppListResult,
    AppShowResult,
    ArtifactResult,
    CheckCommandResult,
    EdgeListResult,
    EdgeShowResult,
    Envelope,
    HelpResult,
    HostListResult,
    HostShowResult,
    InfoResult,
    ResolveResult,
    RootResult,
    SecretsAuditResult,
    SecretsInspectResult,
    ServiceListResult,
    ServiceShowResult,
    ValidateResult,
    VersionResult,
)


ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "src/infralink/schemas/cli/v1"
MODELS: dict[str, Any] = {
    "root": Envelope[RootResult],
    "help": Envelope[HelpResult],
    "version": Envelope[VersionResult],
    "info": Envelope[InfoResult],
    "hosts": Envelope[HostListResult],
    "host-show": Envelope[HostShowResult],
    "services": Envelope[ServiceListResult],
    "service-show": Envelope[ServiceShowResult],
    "edges-list": Envelope[EdgeListResult],
    "edge-show": Envelope[EdgeShowResult],
    "validate": Envelope[ValidateResult],
    "resolve": Envelope[ResolveResult],
    "check": Envelope[CheckCommandResult],
    "app-list": Envelope[AppListResult],
    "app-show": Envelope[AppShowResult],
    "analyze": Envelope[AnalyzeResult],
    "diagram": Envelope[ArtifactResult],
    "docs": Envelope[ArtifactResult],
    "secrets-inspect": Envelope[SecretsInspectResult],
    "secrets-audit": Envelope[SecretsAuditResult],
}


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, model in MODELS.items():
        rendered = json.dumps(
            model.model_json_schema(), indent=2, sort_keys=True
        ) + "\n"
        (OUTPUT / f"{name}.json").write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
```

Add the schema package-data configuration now that its source directory exists:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/infralink/schemas" = "infralink/schemas"
```

- [ ] **Step 5: Generate schemas twice and prove byte determinism**

Run:

```bash
/tmp/infralink-plan-venv/bin/python scripts/generate_cli_schemas.py
/tmp/infralink-plan-venv/bin/python -c \
  "from pathlib import Path; import hashlib, json; p=Path('src/infralink/schemas/cli/v1'); Path('/tmp/infralink-schema-digests.json').write_text(json.dumps({f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.glob('*.json'))}, sort_keys=True))"
/tmp/infralink-plan-venv/bin/python scripts/generate_cli_schemas.py
/tmp/infralink-plan-venv/bin/python -c \
  "from pathlib import Path; import hashlib, json; p=Path('src/infralink/schemas/cli/v1'); expected=json.loads(Path('/tmp/infralink-schema-digests.json').read_text()); actual={f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.glob('*.json'))}; assert actual == expected"
/tmp/infralink-plan-venv/bin/python -m pytest tests/test_cli_contracts.py -q
test -z "$(git ls-files --others --exclude-standard src/infralink/schemas/cli/v1 | grep -vE '\.json$')"
```

Expected: tests pass and a second schema generation produces no diff.

- [ ] **Step 6: Commit typed contracts and schemas**

```bash
git add pyproject.toml src/infralink/cli/contracts.py src/infralink/schemas scripts/generate_cli_schemas.py tests/test_cli_contracts.py
git commit -m "feat(cli): define versioned command contracts"
```

---

### Task 4: Centralize Actions, Errors, Redaction, And Envelopes

**Files:**
- Create: `src/infralink/cli/actions.py`
- Create: `src/infralink/cli/errors.py`
- Modify: `src/infralink/cli/output.py`
- Modify: `tests/test_cli_output.py`

- [ ] **Step 1: Replace ad hoc envelope tests with v1 expectations**

Replace `tests/test_cli_output.py` with tests that assert:

```python
from infralink.cli.actions import action
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.output import command_context, error_envelope, ok_envelope


def test_ok_envelope_contains_structured_command_and_action() -> None:
    payload = ok_envelope(
        context=command_context(
            ["infralink", "validate"],
            path=["validate"],
            args={},
            flags=[],
            resolved={"version": "0.2.0", "cwd": "/work"},
        ),
        result={"valid": True},
        next_actions=[action("check", ["infralink", "check"], "Run checks")],
    )

    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["command"]["parsed"]["path"] == ["validate"]
    assert payload["next_actions"][0]["argv"] == ["infralink", "check"]


def test_sensitive_argv_is_redacted_before_capture() -> None:
    context = command_context(
        ["infralink", "secrets", "audit", "--token", "canary-secret"],
        path=["secrets", "audit"],
        args={},
        flags=[],
        resolved={"version": "0.2.0", "cwd": "/work"},
    )

    assert "canary-secret" not in context.raw
    assert "[REDACTED]" in context.raw


def test_failure_has_stable_exit_and_repair_action() -> None:
    failure = CliFailure(
        code=ErrorCode.ENTITY_NOT_FOUND,
        message="Edge not found",
        exit_code=3,
        fix="Run infralink edges-list",
        details={"entity_type": "edge", "requested_id": "missing"},
        next_actions=[action("list", ["infralink", "edges-list"], "List edges")],
    )
    payload = error_envelope(command_context(["infralink"], [], {}, [], {}), failure)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "entity_not_found"
    assert failure.exit_code == 3
```

- [ ] **Step 2: Run tests and verify the old helper fails**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest tests/test_cli_output.py -q
```

Expected: imports or signatures fail.

- [ ] **Step 3: Add stable errors**

Create `src/infralink/cli/errors.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from infralink.cli.contracts import Action


class ErrorCode(str, Enum):
    USAGE_ERROR = "usage_error"
    INPUT_LOAD_FAILED = "input_load_failed"
    ENTITY_NOT_FOUND = "entity_not_found"
    INVALID_CURSOR = "invalid_cursor"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_AUTHENTICATION_FAILED = "provider_authentication_failed"
    PROVIDER_AUTHORIZATION_FAILED = "provider_authorization_failed"
    PROVIDER_TIMEOUT = "provider_timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class CliFailure(Exception):
    code: ErrorCode
    message: str
    exit_code: int
    fix: str
    details: dict[str, Any] = field(default_factory=dict)
    next_actions: list[Action] = field(default_factory=list)
```

- [ ] **Step 4: Add structured action construction**

Create `src/infralink/cli/actions.py`:

```python
from __future__ import annotations

import shlex

from infralink.cli.contracts import Action, Binding


def action(
    rel: str,
    argv: list[str],
    description: str,
    *,
    bindings: dict[str, Binding] | None = None,
) -> Action:
    active_bindings = bindings or {}
    return Action(
        rel=rel,
        argv=argv,
        command=shlex.join(argv),
        description=description,
        safe=True,
        templated=bool(active_bindings),
        bindings=active_bindings,
    )
```

- [ ] **Step 5: Replace the envelope helper**

Rewrite `src/infralink/cli/output.py` around `Envelope`, with:

```python
SENSITIVE_OPTIONS = {
    "--access-token",
    "--password",
    "--password-env",
    "--token",
}


def redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for value in argv:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        option, separator, inline = value.partition("=")
        if option in SENSITIVE_OPTIONS:
            redacted.append(f"{option}=[REDACTED]" if separator else option)
            redact_next = not separator
            continue
        redacted.append(value)
    return redacted
```

`command_context()` must use `shlex.join(redact_argv(argv))`.
`ok_envelope()` must serialize `Envelope` with `ok=True`.
`error_envelope()` must serialize `Envelope` with `ok=False`, `error`, `fix`,
and no `result`. Both return `dict[str, Any]`.

- [ ] **Step 6: Run focused tests**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest tests/test_cli_output.py -q
```

Expected: all tests pass and the canary is absent from captured output.

- [ ] **Step 7: Commit shared output behavior**

```bash
git add src/infralink/cli/actions.py src/infralink/cli/errors.py src/infralink/cli/output.py tests/test_cli_output.py
git commit -m "feat(cli): centralize safe json envelopes"
```

---

### Task 5: Make Root, Help, Version, And Failures JSON-Only

**Files:**
- Modify: `src/infralink/cli/main.py`
- Create: `tests/test_cli_discovery.py`
- Modify: `tests/test_cli_root.py`
- Modify: `tests/test_cli_validate.py`

- [ ] **Step 1: Write JSON-only discovery and failure tests**

Create `tests/test_cli_discovery.py`:

```python
import json

from click.testing import CliRunner

from infralink.cli.main import cli, main


def invoke(*args: str):
    return CliRunner().invoke(cli, list(args))


def payload_for(*args: str) -> dict:
    result = invoke(*args)
    assert result.output.count("\n") == 1
    return json.loads(result.output)


def test_root_discovers_commands_as_json() -> None:
    payload = payload_for()
    assert payload["result"]["version"] == "0.2.0"
    assert {"help", "version", "hosts", "services", "edges-list"} <= {
        item["name"] for item in payload["result"]["commands"]
    }


def test_help_is_json() -> None:
    payload = payload_for("help", "resolve")
    assert payload["result"]["path"] == ["resolve"]
    assert payload["result"]["arguments"][0]["name"] == "edge_id"

def test_click_help_aliases_are_json() -> None:
    assert payload_for("--help")["result"]["path"] == []
    assert payload_for("resolve", "--help")["result"]["path"] == ["resolve"]


def test_version_is_json() -> None:
    payload = payload_for("version")
    assert payload["result"] == {
        "version": "0.2.0",
        "cli_schema_version": "infralink.cli/v1",
    }

def test_click_version_alias_is_json() -> None:
    assert payload_for("--version")["result"]["version"] == "0.2.0"


def test_unknown_command_is_json_usage_error() -> None:
    result = invoke("not-a-command")
    payload = json.loads(result.output)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "usage_error"


def test_missing_registry_is_json_input_error() -> None:
    result = invoke("--registry", "missing.yml", "info")
    payload = json.loads(result.output)
    assert result.exit_code == 3
    assert payload["error"]["code"] == "input_load_failed"


def test_wrapper_and_click_object_have_identical_parse_errors() -> None:
    direct = invoke("--unknown")
    assert main(["--unknown"]) == direct.exit_code == 2
```

- [ ] **Step 2: Run discovery tests and verify Click prose leaks**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest tests/test_cli_discovery.py -q
```

Expected: failures for missing commands and non-JSON usage handling.

- [ ] **Step 3: Add explicit discovery commands and one command runner**

In `src/infralink/cli/main.py`, implement a `JsonGroup(click.Group)` and use it
as the root `cls`. Override `main()` so every invocation path, including
`CliRunner.invoke(cli, ...)`, runs the same boundary:

1. normalize root `--help` to `help`, `<command> --help` to
   `help <command>`, and root `--version` to `version`
2. call `super().main(..., standalone_mode=False)`
3. catch `click.UsageError`, `CliFailure`, and unexpected exceptions
4. emit exactly one envelope and return/exit with `2`, the failure exit, or
   `70` according to the incoming `standalone_mode`

Remove `@click.version_option`; add explicit `help` and `version` commands to
`COMMAND_METADATA`; and add `services`, `host`, `edge`, `service`, and
`secrets`. Disable Click's prose help with
`context_settings={"help_option_names": []}` only after the alias normalization
is covered. `main(args)` must call `cli.main(args=args, prog_name="infralink",
standalone_mode=False)` and return the resulting integer. `run()` raises
`SystemExit(main())`.

The error conversion must use:

```python
def entity_not_found(entity_type: str, requested_id: str) -> CliFailure:
    discovery = {
        "host": ["infralink", "hosts"],
        "service": ["infralink", "services"],
        "edge": ["infralink", "edges-list"],
        "app": ["infralink", "app", "list"],
    }[entity_type]
    return CliFailure(
        code=ErrorCode.ENTITY_NOT_FOUND,
        message=f"{entity_type.title()} not found",
        exit_code=3,
        fix=f"Run {shlex.join(discovery)}",
        details={"entity_type": entity_type, "requested_id": requested_id},
        next_actions=[action("list", discovery, f"List {entity_type} records")],
    )
```

- [ ] **Step 4: Update the module entry point**

Change `src/infralink/__main__.py` to:

```python
from infralink.cli.main import run


if __name__ == "__main__":
    run()
```

Set `[project.scripts] infralink = "infralink.cli.main:run"`.

- [ ] **Step 5: Run discovery and legacy root tests**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_cli_discovery.py tests/test_cli_root.py tests/test_cli_validate.py -q
```

Expected: all tests pass; each command emits one JSON line.

Also build and install the wheel into a clean venv and invoke both public
entrypoints so wrapper-only behavior cannot escape the tests:

```bash
/tmp/infralink-plan-venv/bin/python -m build
python3 -m venv /tmp/infralink-entry-smoke
/tmp/infralink-entry-smoke/bin/pip install dist/infralink-0.2.0-py3-none-any.whl
/tmp/infralink-entry-smoke/bin/infralink --help | \
  /tmp/infralink-entry-smoke/bin/python -m json.tool >/dev/null
/tmp/infralink-entry-smoke/bin/python -m infralink --version | \
  /tmp/infralink-entry-smoke/bin/python -m json.tool >/dev/null
```

- [ ] **Step 6: Commit JSON-only discovery**

```bash
git add pyproject.toml src/infralink/__main__.py src/infralink/cli/main.py tests/test_cli_discovery.py tests/test_cli_root.py tests/test_cli_validate.py
git commit -m "feat(cli): make discovery and failures json only"
```

---

### Task 6: Add Bounded Pagination And Read-Only Detail Queries

**Files:**
- Create: `src/infralink/cli/pagination.py`
- Create: `src/infralink/cli/queries.py`
- Create: `tests/test_cli_pagination.py`
- Create: `tests/test_cli_queries.py`
- Modify: `src/infralink/cli/main.py`

- [ ] **Step 1: Write cursor integrity tests**

Create `tests/test_cli_pagination.py`:

```python
import pytest

from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.pagination import CursorCodec, page_items


def test_cursor_is_bound_to_command_collection_and_inputs() -> None:
    codec = CursorCodec(key=b"test-only-key")
    cursor = codec.encode(
        command="validate",
        collection="errors",
        offset=100,
        fingerprint="registry-sha",
    )

    assert codec.decode(cursor, "validate", "errors", "registry-sha") == 100
    with pytest.raises(CliFailure) as error:
        codec.decode(cursor, "validate", "warnings", "registry-sha")
    assert error.value.code == ErrorCode.INVALID_CURSOR


def test_page_items_never_exceeds_requested_limit() -> None:
    page = page_items(list(range(150)), limit=100, offset=0, next_cursor="next")
    assert len(page.items) == 100
    assert page.page.returned == 100
    assert page.page.total == 150
    assert page.page.next_cursor == "next"
```

- [ ] **Step 2: Implement signed opaque cursors**

Create `src/infralink/cli/pagination.py` using URL-safe base64 and HMAC-SHA256.
The serialized payload is:

```python
{
    "v": 1,
    "command": command,
    "collection": collection,
    "offset": offset,
    "fingerprint": fingerprint,
}
```

Reject invalid base64, signature mismatch, wrong version, command, collection,
fingerprint, negative offset, and limits outside `1..1000` with
`CliFailure(ErrorCode.INVALID_CURSOR, exit_code=2)`. Use a process-local key
derived from `sha256(b"infralink.cli/v1")`; cursors are integrity tokens, not
secrets.

- [ ] **Step 3: Write query serializer tests**

Create `tests/test_cli_queries.py` with sanitized fixtures and assertions:

```python
def test_host_summary_truncates_relationship_preview(registry) -> None:
    host = registry.get("test-host")
    summary = host_summary(host, service_preview_limit=1)

    assert summary.service_count == 2
    assert len(summary.services) == 1
    assert summary.services_truncated is True


def test_host_show_returns_complete_page(registry) -> None:
    result = show_host(registry, "test-host", collection="services", limit=100)

    assert result.host.id == registry.get("test-host").uuid
    assert result.services.page.total == 2


def test_missing_edge_uses_shared_not_found_error(registry, edges) -> None:
    with pytest.raises(CliFailure) as error:
        show_edge(edges, "missing")
    assert error.value.code == ErrorCode.ENTITY_NOT_FOUND
```

- [ ] **Step 4: Implement read-only serializers and queries**

Create `src/infralink/cli/queries.py` with pure functions:

```python
def host_summary(host: Host, service_preview_limit: int = 128) -> HostSummary:
    services = sorted(host.service_names)
    projects = sorted(host.projects)
    return HostSummary(
        id=host.uuid,
        canonical_name=host.canonical_name,
        status=host.status.value,
        service_count=len(services),
        services=services[:service_preview_limit],
        services_truncated=len(services) > service_preview_limit,
        project_count=len(projects),
        projects=projects[:64],
        projects_truncated=len(projects) > 64,
    )
```

Add `service_summary`, `edge_summary`, `list_hosts`, `show_host`,
`list_services`, `show_service`, `list_edges`, `show_edge`, `list_apps`, and
`show_app`. Use sorted stable identifiers before pagination. For multi-page
detail commands, `--collection` selects which cursor advances while other
collections return their deterministic first page.

- [ ] **Step 5: Wire list and detail commands**

Update `src/infralink/cli/main.py` so these commands return the typed results:

```text
hosts
host show <host-id>
services
service show <service-id>
edges-list
edge show <edge-id>
app list
app show <app-id>
```

Every pageable command accepts:

```python
@click.option("--limit", type=click.IntRange(1, 1000), default=100)
@click.option("--cursor")
@click.option("--collection")
```

Reject a cursor without `--collection` only when the result has multiple paged
fields. Add continuation actions with structured `argv`, typed cursor binding,
and the matching collection name.

- [ ] **Step 6: Run query and pagination tests**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_cli_pagination.py tests/test_cli_queries.py \
  tests/test_cli_commands_json.py tests/test_cli_json.py -q
```

Expected: all tests pass; the old top-level `hosts` compatibility assertion is
updated to read `payload["result"]["items"]`.

- [ ] **Step 7: Commit query contracts**

```bash
git add src/infralink/cli/main.py src/infralink/cli/pagination.py src/infralink/cli/queries.py tests/test_cli_pagination.py tests/test_cli_queries.py tests/test_cli_commands_json.py tests/test_cli_json.py
git commit -m "feat(cli): add bounded topology queries"
```

---

### Task 7: Normalize Validation And Health Outcomes

**Files:**
- Modify: `src/infralink/cli/validate.py`
- Modify: `src/infralink/cli/check.py`
- Modify: `src/infralink/health/checks.py`
- Create: `tests/test_cli_check.py`
- Modify: `tests/test_cli_validate.py`

- [ ] **Step 1: Write tests for completed negative domain results**

Add tests that assert:

```python
def test_invalid_topology_is_completed_negative_result(tmp_path) -> None:
    result = invoke_with_invalid_edge(tmp_path, "validate")
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is True
    assert payload["result"]["valid"] is False
    assert payload["error"] if "error" in payload else None is None


def test_unhealthy_edge_is_completed_negative_result(monkeypatch, topology) -> None:
    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: HealthCheckResult(
            edge_id=edge.id,
            healthy=False,
            latency_ms=None,
            error="connection refused",
            criticality="high",
        ),
    )
    result = topology.invoke("check")
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is True
    assert payload["result"]["healthy"] is False
```

- [ ] **Step 2: Run the tests and observe old error envelopes**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_cli_validate.py tests/test_cli_check.py -q
```

Expected: invalid and unhealthy results currently report `ok: false`.

- [ ] **Step 3: Return typed diagnostics and checks**

Refactor `validate` to convert every error or warning into:

```python
Diagnostic(
    code="target_host_not_found",
    path=f"edges.{edge.id}.to.host",
    message=f"Target host not found: {edge.target_host}",
    severity="error",
)
```

Return `ValidateResult(valid=False, ...)` through `ok_envelope()` and terminate
with exit `1`. Loading errors remain `CliFailure(INPUT_LOAD_FAILED, exit=3)`.

Refactor `check` to return `CheckCommandResult(healthy=False, checks=...,
summary=...)` through `ok_envelope()` and exit `1` for any unhealthy result.
Remove the special exit `2` for critical failures because exit `2` is reserved
for usage errors.

- [ ] **Step 4: Bound both result collections**

Apply `--limit`, `--cursor`, and `--collection` to `validate.errors`,
`validate.warnings`, and `check.checks`. Continuation commands must preserve
`--strict`, `--check-resolution`, check filters, and selected input paths.

- [ ] **Step 5: Run validation and health tests**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_cli_validate.py tests/test_cli_check.py tests/test_resolver.py -q
```

Expected: all pass; no warning prose is written to stderr.

- [ ] **Step 6: Commit normalized outcomes**

```bash
git add src/infralink/cli/validate.py src/infralink/cli/check.py src/infralink/health/checks.py tests/test_cli_validate.py tests/test_cli_check.py
git commit -m "feat(cli): normalize validation and health outcomes"
```

---

### Task 8: Add Opaque Secrets And Safe Resolution

**Files:**
- Create: `src/infralink/secrets/__init__.py`
- Create: `src/infralink/secrets/base.py`
- Create: `src/infralink/secrets/inventory.py`
- Modify: `src/infralink/core/resolver.py`
- Modify: `src/infralink/cli/resolve.py`
- Create: `tests/test_secret_value.py`
- Create: `tests/test_secret_inventory.py`
- Modify: `tests/test_resolver.py`

- [ ] **Step 1: Write opaque-value tests with a canary**

Create `tests/test_secret_value.py`:

```python
import json

import pytest

from infralink.secrets import SecretValue


CANARY = "infralink-secret-canary-47291"


def test_secret_value_is_redacted_and_not_json_serializable() -> None:
    secret = SecretValue(CANARY)

    assert str(secret) == "[REDACTED]"
    assert repr(secret) == "SecretValue([REDACTED])"
    with pytest.raises(TypeError):
        f"{secret}"
    with pytest.raises(TypeError):
        json.dumps({"secret": secret})
    assert secret.reveal() == CANARY
```

- [ ] **Step 2: Implement the provider-neutral secret contract**

Create `src/infralink/secrets/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SecretValue:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def __str__(self) -> str:
        return "[REDACTED]"

    def __repr__(self) -> str:
        return "SecretValue([REDACTED])"

    def __format__(self, format_spec: str) -> str:
        raise TypeError("SecretValue requires explicit reveal()")

    def reveal(self) -> str:
        return self.__value


@dataclass(frozen=True)
class SecretReference:
    ref: str
    project: str | None
    locations: tuple[str, ...]
    required: bool = True


@dataclass(frozen=True)
class SecretAudit:
    ref: str
    project: str | None
    present: bool | None
    accessible: bool | None
    error_code: str | None = None


class SecretResolver(Protocol):
    def resolve(self, reference: SecretReference) -> SecretValue: ...

    def audit(self, references: list[SecretReference]) -> list[SecretAudit]: ...
```

Export these four names from `src/infralink/secrets/__init__.py` and
`src/infralink/__init__.py`.

- [ ] **Step 3: Extract declared references without provider access**

Create `src/infralink/secrets/inventory.py`. For each edge with
`edge.secret_ref`, associate the target host's `bws_project` and a location:

```python
def collect_secret_references(
    registry: Registry, edges: EdgeSet
) -> list[SecretReference]:
    grouped: dict[tuple[str, str | None], list[str]] = {}
    for edge in edges:
        if not edge.secret_ref:
            continue
        target = registry.get_by_uuid(edge.target_host)
        project = target.bws_project if target else None
        grouped.setdefault((edge.secret_ref, project), []).append(
            f"edges.{edge.id}.auth.secret_ref"
        )
    return [
        SecretReference(ref=ref, project=project, locations=tuple(sorted(locations)))
        for (ref, project), locations in sorted(grouped.items())
    ]
```

Add `Host.bws_project` and `Host.bws_extra_projects` read-only properties in
`core/registry.py`.

- [ ] **Step 4: Remove credential serialization from the CLI**

Delete `--password`, `--password-env`, and password-bearing URL output from
`src/infralink/cli/resolve.py`. Keep endpoint/IP preference as safe inputs.
Return:

```python
ResolveResult(
    edge=edge_summary(edge),
    endpoint=Endpoint(host=ip, port=edge.target_port, protocol=edge.protocol),
    connection_template=resolver.get_connection_template(
        edge_id, user=user, database=database, prefer_ip=prefer_ip
    ),
    secret_refs=page_items(
        [edge.secret_ref] if edge.secret_ref else [],
        limit=100,
        offset=0,
        next_cursor=None,
    ),
)
```

Add `EdgeResolver.get_connection_template()` that inserts
`${secret:<reference>}` instead of a value. Retain existing Python URL methods
for compatibility, but mark them deprecated and require callers to pass a
plain string or explicitly revealed `SecretValue`; none are exported by CLI.

- [ ] **Step 5: Add a repository-wide leak regression**

Add a test that invokes every CLI command with fixtures containing `CANARY` and
asserts the canary is absent from `result.output`, `result.stderr`, exception
text, and serialized payloads.

- [ ] **Step 6: Run secret and resolver tests**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_secret_value.py tests/test_secret_inventory.py \
  tests/test_resolver.py tests/test_cli_commands_json.py -q
```

Expected: all pass; the canary never appears.

- [ ] **Step 7: Commit safe resolution**

```bash
git add src/infralink/__init__.py src/infralink/core/registry.py src/infralink/core/resolver.py src/infralink/cli/resolve.py src/infralink/secrets tests/test_secret_value.py tests/test_secret_inventory.py tests/test_resolver.py tests/test_cli_commands_json.py
git commit -m "feat(secrets): add opaque resolution boundary"
```

---

### Task 9: Add The Optional Hosted BWS Adapter And Secret Commands

**Files:**
- Create: `src/infralink/adapters/__init__.py`
- Create: `src/infralink/adapters/bws.py`
- Create: `src/infralink/cli/secrets.py`
- Create: `tests/test_bws_adapter.py`
- Create: `tests/test_cli_secrets.py`
- Modify: `src/infralink/cli/main.py`

- [ ] **Step 1: Write hosted-only configuration and fake-SDK tests**

Use an injected `SdkFactory` so default tests never contact Bitwarden. Cover:

```python
def test_hosted_endpoints_are_default() -> None:
    config = BwsConfig.from_env(
        {
            "BWS_ACCESS_TOKEN": "test-token",
            "BWS_ORGANIZATION_ID": "11111111-1111-1111-1111-111111111111",
        }
    )
    assert config.api_url == "https://api.bitwarden.com"
    assert config.identity_url == "https://identity.bitwarden.com"


@pytest.mark.parametrize(
    "name",
    ["BWS_API_URL", "BWS_IDENTITY_URL", "BWS_TRUSTED_HOSTS"],
)
def test_endpoint_overrides_are_rejected_in_production(name: str) -> None:
    with pytest.raises(BwsConfigurationError):
        BwsConfig.from_env(
            {
                "BWS_ACCESS_TOKEN": "real-token-shape",
                "BWS_ORGANIZATION_ID": "11111111-1111-1111-1111-111111111111",
                name: "http://127.0.0.1:8080",
            }
        )


def test_loopback_config_requires_injected_fake_sdk() -> None:
    config = BwsConfig.for_test(
        access_token="INFRALINK_FAKE_BWS_TOKEN",
        organization_id="11111111-1111-1111-1111-111111111111",
        api_url="http://127.0.0.1:8080",
        identity_url="http://127.0.0.1:8081",
    )
    with pytest.raises(BwsConfigurationError):
        BwsSecretResolver(config=config)


def test_audit_never_reads_secret_values(fake_sdk) -> None:
    adapter = BwsSecretResolver(config=fake_sdk.config, sdk_factory=fake_sdk.factory)
    result = adapter.audit([declared_reference("db_password")])
    assert result[0].present is True
    assert fake_sdk.secret_get_calls == []
```

The fake SDK must model login, project listing, secret identifier listing, and
secret retrieval. Include tests for provider-wide login denial, no accessible
configured project, partial project visibility, absent reference, timeout,
malformed response, and canary redaction. Do not claim that absence from
`secrets().list()` distinguishes nonexistent from object-level authorization
denial; expose the honest state `unavailable_or_missing`.

- [ ] **Step 2: Implement immutable hosted-only production configuration**

Create `BwsConfig` in `src/infralink/adapters/bws.py`:

```python
@dataclass(frozen=True)
class BwsConfig:
    access_token: str
    organization_id: str
    api_url: str = "https://api.bitwarden.com"
    identity_url: str = "https://identity.bitwarden.com"
    test_only: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> BwsConfig:
        token = env.get("BWS_ACCESS_TOKEN", "")
        organization_id = env.get("BWS_ORGANIZATION_ID", "")
        if not token or not organization_id:
            raise BwsConfigurationError(
                "BWS_ACCESS_TOKEN and BWS_ORGANIZATION_ID are required"
            )
        forbidden = {"BWS_API_URL", "BWS_IDENTITY_URL", "BWS_TRUSTED_HOSTS"} & env.keys()
        if forbidden:
            raise BwsConfigurationError(
                "endpoint overrides are unsupported by infralink 0.2"
            )
        return cls(
            access_token=token,
            organization_id=organization_id,
        )
```

Add a private/test-facing `BwsConfig.for_test()` that accepts only the literal
token `INFRALINK_FAKE_BWS_TOKEN`, requires loopback hosts, sets `test_only=True`,
and can be used only when an injected `SdkFactory` is passed. Production
`from_env()` always selects the two hosted HTTPS origins. The Bitwarden Python
SDK 2.1 exposes no redirect-policy or response-origin hook, so `v0.2.0` must not
claim enforceable custom-origin redirect guarantees. Record this constrained
implementation in the adapter docstring and release notes; custom endpoints
remain deferred until the SDK provides a controllable transport.

- [ ] **Step 3: Implement the read-only adapter**

Import `bitwarden_sdk` inside the default factory so base-package imports work
without the extra. Use the SDK 2.1 surface:

```python
client = BitwardenClient(
    client_settings_from_dict(
        {
            "apiUrl": config.api_url,
            "identityUrl": config.identity_url,
            "deviceType": DeviceType.SDK,
            "userAgent": "infralink/0.2.0",
        }
    )
)
login = client.auth().login_access_token(config.access_token)
projects = client.projects().list(config.organization_id)
identifiers = client.secrets().list(config.organization_id)
secret = client.secrets().get(str(identifier.id))
```

Never call `create`, `update`, or `delete`. `audit()` uses only projects and
secret identifiers, so it never retrieves values. `resolve()` calls `get()` for
one declared reference and immediately wraps `response.data.value` in
`SecretValue`.

Map response stage to public behavior:

- failed login or zero accessible configured projects: provider failure, exit 4
- some configured projects not returned: `project_unavailable` records
- reference absent from accessible identifier metadata:
  `unavailable_or_missing` negative audit record
- ambiguous denial: provider failure, no partial result

- [ ] **Step 4: Add metadata-only secret commands**

Create `src/infralink/cli/secrets.py`:

- `inspect` calls `collect_secret_references()` only
- `audit --provider bws` loads `BwsConfig`, lazily imports the adapter, and
  audits only collected references
- `--ref` must select an already-declared reference or return
  `entity_not_found`
- no option accepts a token or arbitrary secret ID
- missing `bitwarden_sdk` maps to `provider_unavailable`, exit 4, with
  `python -m pip install 'infralink[bws]'` as the next action
- provider-wide failures return `ok: false`, exit 4
- missing/denied required references return `ok: true`, exit 1

- [ ] **Step 5: Run BWS and CLI secret tests**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_bws_adapter.py tests/test_cli_secrets.py \
  tests/test_secret_value.py tests/test_secret_inventory.py -q
```

Expected: all tests pass without network access.

- [ ] **Step 6: Verify base and optional imports separately**

Run:

```bash
python3 -m venv /tmp/infralink-base-smoke
/tmp/infralink-base-smoke/bin/pip install .
/tmp/infralink-base-smoke/bin/python -c "import infralink; print(infralink.__version__)"
/tmp/infralink-base-smoke/bin/python -c "import importlib.util; assert importlib.util.find_spec('bitwarden_sdk') is None"
```

Expected: `0.2.0`; base import works without Bitwarden.

- [ ] **Step 7: Commit the optional adapter**

```bash
git add src/infralink/adapters src/infralink/cli/main.py src/infralink/cli/secrets.py tests/test_bws_adapter.py tests/test_cli_secrets.py
git commit -m "feat(bws): add optional read-only secrets adapter"
```

---

### Task 10: Migrate Artifact Commands To `infralink.cli/v1` And Complete Contract Coverage

**Files:**
- Modify: `src/infralink/cli/analyze.py`
- Modify: `src/infralink/cli/diagram.py`
- Modify: `src/infralink/cli/docs.py`
- Modify: `src/infralink/cli/app.py`
- Create: `tests/test_cli_artifacts.py`
- Modify: `tests/test_cli_commands_json.py`

- [ ] **Step 1: Write artifact contract tests**

Create `tests/test_cli_artifacts.py` with one test per command:

```python
def assert_artifact(artifact: dict, root: Path) -> None:
    path = root / artifact["path"]
    assert path.is_file()
    assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert artifact["media_type"]


def test_diagram_requires_explicit_output_and_reports_digest(topology) -> None:
    result = topology.invoke("diagram", "--output", "out", "--format", "d2")
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["ok"] is True
    assert_artifact(payload["result"]["artifacts"]["items"][0], topology.root)
```

Add equivalent tests for `docs` and `analyze`. Add failure tests proving no
output directory is created when the input fails.

- [ ] **Step 2: Return artifact metadata, not generated content**

Replace `--stdout` content output with an input error directing callers to an
explicit output path. For every generated file, return:

```python
Artifact(
    path=str(path),
    media_type=media_type,
    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
)
```

Use `Page[Artifact]` and the shared artifact summary. Do not include generated
document bodies in JSON.

- [ ] **Step 3: Convert remaining commands to shared envelopes**

Remove direct `json.dumps`, local error codes, and direct `SystemExit` from
`analyze.py`, `diagram.py`, `docs.py`, and `app.py`. Each command returns its
typed result or raises `CliFailure`; the shared runner serializes and exits.
Ensure `analyze` diagnostics and artifacts, `app show` services and edges, and
artifact lists use the common paging options.

- [ ] **Step 4: Validate every command against its generated schema**

In `tests/cli_helpers.py`, add:

```python
def assert_schema(command_schema: str, payload: dict) -> None:
    schema = json.loads(
        (
            ROOT
            / "src/infralink/schemas/cli/v1"
            / f"{command_schema}.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(payload)
```

Call it from root, help, version, info, every list/detail command, validate,
resolve, check, app, analyze, diagram, docs, and secrets tests.

- [ ] **Step 5: Run all CLI tests**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest tests/test_cli_*.py -q
```

Expected: all CLI responses validate; no command writes prose to stderr.

- [ ] **Step 6: Commit remaining command migration**

```bash
git add src/infralink/cli tests/cli_helpers.py tests/test_cli_artifacts.py tests/test_cli_commands_json.py
git commit -m "feat(cli): complete v1 command migration"
```

---

### Task 11: Make Ruff, Mypy, And Coverage Release Gates

**Files:**
- Modify: `src/infralink/core/schema.py`
- Modify: `src/infralink/core/edges.py`
- Modify: `src/infralink/core/application.py`
- Modify: `src/infralink/core/registry.py`
- Modify: `src/infralink/core/resolver.py`
- Modify: `src/infralink/health/checks.py`
- Modify: `src/infralink/generators/{mermaid,d2,dot,markdown}.py`
- Modify: `src/infralink/validation.py`
- Modify: `tests/*.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Apply only Ruff's safe mechanical fixes**

Run:

```bash
/tmp/infralink-plan-venv/bin/ruff check src tests --fix
/tmp/infralink-plan-venv/bin/ruff format src tests
```

Expected: import ordering, whitespace, unused imports, and modern typing are
fixed. Run the focused tests before committing; behavioral changes are outside
this mechanical step and must be handled test-first in a later step.

- [ ] **Step 2: Fix explicit health-check typing**

Add a declared excluded field to `HealthCheckConfig`:

```python
    explicit: bool = Field(default=False, exclude=True)
```

Replace `setattr` in `Edge.healthcheck` with:

```python
        if self._schema.healthcheck is None:
            return HealthCheckConfig(explicit=False)
        return self._schema.healthcheck.model_copy(update={"explicit": True})
```

- [ ] **Step 3: Fix remaining strict typing defects**

Make these concrete corrections:

- type schema normalization validators as `dict[str, Any]` at the
  `mode="before"` boundary
- make `Edge.target_port` raise `ResolutionError` when the schema port is
  absent rather than return `None` as `int`
- replace `Application.resolve_edges()` use of nonexistent `get_edge()` with
  `get()`, filtering `None`
- type health-check selections as `list[Edge]`
- decode HTTP response bytes before formatting errors
- correct Mermaid's seen-edge tuple type to four strings
- iterate over `host.services.items()` rather than slicing a dictionary in the
  Markdown generator
- export `EdgeType` from `core.schema`, not indirectly from `core.edges`
- annotate all CLI loader and output helper return types

- [ ] **Step 4: Add focused tests for each behavioral type fix**

Add tests for absent target port, missing application edge, decoded HTTP error,
and duplicate Mermaid edge handling. Run each test before and after its minimal
fix.

- [ ] **Step 5: Enforce the approved coverage threshold**

Set:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --cov=infralink --cov-branch --cov-report=term-missing --cov-fail-under=70"
```

Add focused unit tests until `SecretValue`, CLI redaction, and the BWS adapter
each report 100% branch coverage and overall coverage is at least 70%.

- [ ] **Step 6: Run all local quality gates**

Run:

```bash
/tmp/infralink-plan-venv/bin/ruff format --check src tests
/tmp/infralink-plan-venv/bin/ruff check src tests
/tmp/infralink-plan-venv/bin/mypy src
/tmp/infralink-plan-venv/bin/python -m pytest
```

Expected: zero Ruff findings, `Success: no issues found`, all tests pass, and
coverage is at least 70%.

- [ ] **Step 7: Commit quality cleanup**

```bash
git add pyproject.toml src tests
git commit -m "test: enforce infralink quality gates"
```

---

### Task 12: Build And Attest A Public Release Candidate

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/release-candidate.yml`
- Create: `.gitleaks.toml`
- Create: `scripts/build_release_manifest.py`
- Create: `tests/test_release_manifest.py`
- Create: `tests/test_release_workflow_policy.py`
- Create: `tests/test_public_data_boundary.py`
- Modify: `README.md`
- Modify: `PRD.md`
- Modify: `BACKLOG.md`
- Create: `docs/compatibility/v0.2.md`

- [ ] **Step 1: Test release-manifest determinism**

Create a test that builds a temporary wheel and sdist fixture, calls
`build_manifest()`, and asserts:

```python
assert manifest == {
    "version": "0.2.0",
    "source_commit": "a" * 40,
    "workflow_run_id": "12345",
    "artifacts": [
        {"name": "infralink-0.2.0-py3-none-any.whl", "sha256": expected_wheel},
        {"name": "infralink-0.2.0.tar.gz", "sha256": expected_sdist},
    ],
}
```

Sort artifacts by name and terminate if version differs, an artifact is
missing, or a filename version differs. The pure manifest builder does not
inspect Git; workflow policy performs the clean-tree check before building.

- [ ] **Step 2: Implement the manifest builder**

Create `scripts/build_release_manifest.py` with a pure `build_manifest()` and a
CLI that reads:

```text
--dist dist/
--source-commit $GITHUB_SHA
--workflow-run-id $GITHUB_RUN_ID
--output dist/manifest.json
```

Write canonical JSON with sorted keys, two-space indentation, and a trailing
newline. The same CLI writes `dist/SHA256SUMS` from the manifest in sorted
filename order. Unit tests assert both files are byte-deterministic.

- [ ] **Step 3: Write failing public-boundary and workflow-policy tests**

In `tests/test_public_data_boundary.py`, recursively inspect tracked public
examples and documentation. Reject IPv4/IPv6 literals, `.i.cyberstorm.dev`,
GCP project identifiers, UUID-shaped BWS project IDs, credential URL userinfo,
and names listed in a test-only forbidden fixture. Permit only RFC 5737
addresses and `example.com` names. Add one failing fixture for every rule.

In `tests/test_release_workflow_policy.py`, parse workflow YAML with
`yaml.BaseLoader` so `on` remains a string. Before creating the workflow,
assert:

```python
assert "workflow_dispatch" in candidate["on"]
assert candidate_permissions["id-token"] == "write"
assert candidate_permissions["attestations"] == "write"
assert "actions/attest-build-provenance@" in candidate_text
assert candidate_text.count("python -m build") == 1
```

The initial test fails because the candidate workflow does not exist.

- [ ] **Step 4: Expand public CI and security gates**

Update `.github/workflows/ci.yml` to:

- test Python `3.10`, `3.11`, and `3.12`
- install `.[dev]`
- explicitly import `infralink.cli.errors.ErrorCode` on Python 3.10
- generate schemas twice, compare digests, and fail on tracked or untracked
  schema drift
- run Ruff format, Ruff lint, strict mypy, and pytest
- build wheel and sdist once on Python 3.12
- run Twine check
- install the wheel into a new virtual environment
- run `python -m infralink version` and the `infralink version` entry point
- scan output and artifacts for the test canary
- run `gitleaks detect --source . --no-banner --redact` through a pinned
  `gitleaks/gitleaks-action` release
- run `tests/test_public_data_boundary.py`

Do not suppress install errors with `|| pip install -e .`.

- [ ] **Step 5: Add the build-once attested candidate workflow**

Create `.github/workflows/release-candidate.yml`, triggered by
`workflow_dispatch`, that:

1. accepts a required full `source_sha` input and requires the operator to
   choose that same commit/ref in GitHub's **Run workflow** ref selector
2. fails unless `inputs.source_sha == github.sha`, checks out
   `${{ github.sha }}`, and verifies both `git rev-parse HEAD` equality and an
   empty `git status --porcelain`
3. runs the complete lint, type, test, schema, and security gates without the
   package-build step
4. builds wheel and sdist exactly once
5. creates `manifest.json` and `SHA256SUMS` with the tested script
6. grants only `contents: read`, `id-token: write`, and
   `attestations: write`
7. invokes pinned `actions/attest-build-provenance@v3` for the wheel, sdist,
   manifest, and checksums
8. uploads `dist/` as artifact name
   `infralink-v0.2.0-${{ github.sha }}` with retention sufficient for the
   private gate
9. resolves and records the numeric Actions artifact ID, source SHA, run ID,
   and digests in the job summary

Use immutable commit SHAs for all third-party actions. The policy test rejects
any second build command, missing attestation permission, or absence of the
`inputs.source_sha == github.sha` guard. Pass the verified
`git rev-parse HEAD` to the manifest builder; after the equality guard it is
the same commit represented by GitHub's signed provenance context.

- [ ] **Step 6: Record actual consumer inventory**

Run:

```bash
rg -n --hidden -S \
  "infralink |from infralink|import infralink|get_postgres_url|get_redis_url|get_url" \
  /root/src/infra-management /root/src/infra-management-registry-main \
  --glob '!**/.git/**' --glob '!**/venv/**' --glob '!**/.venv/**'
```

Write the safe command/import results and required migrations to
`docs/compatibility/v0.2.md`. Do not copy private topology values, hostnames,
addresses, project identifiers, or secret names into the public document.

- [ ] **Step 7: Update public documentation**

Replace password-bearing README/PRD examples with connection templates and
`secret_ref`. Mark the backlog items implemented by the plan. Document:

- JSON-only CLI
- exit codes
- `infralink[bws]`
- hosted BWS environment requirements
- no arbitrary secret lookup
- GitHub candidate artifact adoption
- previous revision rollback
- hosted-only BWS endpoints in `v0.2.0`; custom endpoints remain deferred

- [ ] **Step 8: Commit implementation, then run local candidate checks**

Commit before invoking the clean-source manifest check:

```bash
git add .github .gitleaks.toml pyproject.toml scripts tests README.md PRD.md BACKLOG.md docs/compatibility/v0.2.md
git commit -m "ci: build attested infralink candidates"
test -z "$(git status --porcelain)"
rm -rf dist build
/tmp/infralink-plan-venv/bin/python -m pytest
/tmp/infralink-plan-venv/bin/python -m build
/tmp/infralink-plan-venv/bin/python -m twine check dist/*
/tmp/infralink-plan-venv/bin/python scripts/build_release_manifest.py \
  --dist dist \
  --source-commit "$(git rev-parse HEAD)" \
  --workflow-run-id local \
  --output dist/manifest.json
sha256sum -c dist/SHA256SUMS
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_release_manifest.py tests/test_release_workflow_policy.py \
  tests/test_public_data_boundary.py -q
```

Expected: all pass; manifest source commit equals `git rev-parse HEAD`.

---

### Task 13: Add The Non-Deploying Private Woodpecker Gate

**Repository:** `/root/src/infra-management`

**Files:**
- Create: `scripts/verify_infralink_candidate.py`
- Create: `tests/test_infralink_candidate_contract.py`
- Create: `tests/test_woodpecker_infralink_policy.py`
- Modify: `.woodpecker.yml`

- [ ] **Step 1: Create a clean infra-management worktree**

Run:

```bash
cd /root/src/infra-management
mkdir -p /root/src/.worktrees
git fetch origin main
test "$(git rev-parse origin/main)" = \
  "$(git ls-remote origin refs/heads/main | cut -f1)"
git worktree add /root/src/.worktrees/infra-management-infralink-gate \
  -b feat/infralink-v0.2-gate origin/main
cd /root/src/.worktrees/infra-management-infralink-gate
```

Expected: a clean branch based on current `main`; do not use the dirty primary
worktree.

- [ ] **Step 2: Write manifest-verification tests**

Test these conditions in `tests/test_infralink_candidate_contract.py`:

```python
def test_rejects_digest_mismatch(candidate_bundle) -> None:
    candidate_bundle.wheel.write_bytes(b"tampered")
    with pytest.raises(CandidateVerificationError, match="digest mismatch"):
        verify_candidate(candidate_bundle.root)


def test_rejects_source_commit_mismatch(candidate_bundle) -> None:
    with pytest.raises(CandidateVerificationError, match="source commit"):
        verify_candidate(candidate_bundle.root, expected_source_commit="b" * 40)
```

Also assert version `0.2.0`, exactly one wheel and one sdist, and no unexpected
files beyond the manifest, checksums, and GitHub attestation bundle.

- [ ] **Step 3: Implement exact-byte verification**

Create `scripts/verify_infralink_candidate.py` that:

- parses `manifest.json` with `json`
- validates SHA-256 for every artifact
- validates expected source SHA, workflow run ID, and version from arguments
- invokes `gh attestation verify` for the wheel, sdist, `manifest.json`, and
  `SHA256SUMS` against `cyberstorm-dev/infralink` before parsing the manifest
- prints only a JSON success or failure envelope
- never prints environment variables or archive contents
- returns the verified wheel path for the caller

- [ ] **Step 4: Record and run private consumer contracts**

Add tests that install the verified wheel into a temporary virtual environment
and execute these exact safe workflows against the authoritative checkout:

```text
infralink --registry registry/hosts --edges registry/network/main-dev/edges/edges.yml validate
infralink --registry registry/hosts --edges registry/network/main-dev/edges/edges.yml info
infralink --registry registry/hosts --edges registry/network/main-dev/edges/edges.yml secrets inspect
```

Clone Gitea repository `relaxgg/infra-registry` at `main` into `registry/`
using a read-only token. Initialize `third-party/infralink` only for legacy
comparison tests. Set
`CANDIDATE_SITE=$("$VIRTUAL_ENV/bin/python" -c
"import site; print(site.getsitepackages()[0])")` and
`INFRALINK_SRC="$CANDIDATE_SITE"` for loader-based consumers. Assert
`Path(infralink.__file__)` is inside the candidate venv, not
`third-party/infralink/src`. Install infra-management test dependencies and
`pytest` into that same venv and invoke its exact Python executable.
Assert each stdout is one JSON document with schema version
`infralink.cli/v1`; do not copy private inputs into artifacts.

- [ ] **Step 5: Write a failing Woodpecker isolation policy test**

Parse `.woodpecker.yml` and assert:

- the candidate step selects `event: manual` and requires
  `INFRALINK_CANDIDATE_GATE == "1"`
- every step using `appleboy/drone-ssh`, SSH, `self-deploy.sh`, `rsync`,
  service restart, or a deployment host explicitly requires
  `INFRALINK_CANDIDATE_GATE != "1"`
- the serialized candidate step contains none of `ssh`, `drone-ssh`,
  `self-deploy`, `docker compose`, `rsync`, or deployment host addresses
- the candidate step commands contain no registry or production-service
  mutation

Test the predicate evaluator with both environments: an ordinary manual event
still selects the existing manual steps, while a manual event with
`INFRALINK_CANDIDATE_GATE=1` selects the candidate step and zero deployment
steps. This preserves current manual users and prevents the dangerous
top-level manual trigger from reaching production during candidate validation.
The test must model Woodpecker's semantics: list entries are ORed and fields
inside one entry are ANDed. Validate the finished file with the pinned official
parser as well:

```bash
docker run --rm -v "$PWD:/repo" -w /repo \
  woodpeckerci/woodpecker-cli:v3.15.0 lint .woodpecker.yml
```

- [ ] **Step 6: Add the discriminated manual Woodpecker gate**

Modify `.woodpecker.yml` with these exact combined mappings; never add an
`evaluate` expression as its own list item:

```yaml
steps:
  deploy:
    when:
      - event: push
        branch: main
      - event: manual
        evaluate: 'INFRALINK_CANDIDATE_GATE != "1"'

  deploy-woodpecker:
    when:
      - event: push
        path: "hosts/9157ddeb-cb6d-4d55-8252-9db358f5d932/**"
      - event: manual
        evaluate: 'INFRALINK_CANDIDATE_GATE != "1"'

  verify-infralink-candidate:
    when:
      - event: manual
        evaluate: 'INFRALINK_CANDIDATE_GATE == "1"'
```

Retain the existing settings and commands under the two deployment steps. The
candidate step must:

- require `INFRALINK_ARTIFACT_ID`, `INFRALINK_SOURCE_SHA`,
  `INFRALINK_WORKFLOW_RUN_ID`, and `INFRALINK_VERSION=0.2.0`
- download the immutable GitHub Actions artifact by numeric artifact ID using a
  read-only fine-grained GitHub token and the Actions artifact API
- query the artifact metadata first and require its workflow run ID and name to
  equal the requested candidate values before downloading
- verify GitHub build-provenance attestations for the wheel, sdist,
  `manifest.json`, and `SHA256SUMS`
- run `verify_infralink_candidate.py`
- install the verified wheel in an isolated venv
- run `tests/test_infralink_candidate_contract.py`
- write canonical `woodpecker-evidence.json` containing Woodpecker repository,
  pipeline number, source SHA, candidate run/artifact IDs, artifact digests,
  contract-test digest, and pass/fail
- sign it with the Woodpecker-only Cosign key and push the JSON and signature
  bundle as an OCI artifact to
  `ghcr.io/cyberstorm-dev/infralink-gate-evidence:<source-sha>`
- record the immutable OCI manifest digest
- contain no SSH plugin, deploy command, topology-registry mutation, or
  self-deploy call

The GHCR package contains metadata only. Grant Woodpecker write access only to
that package and the release environment read access. Store the Cosign private
key only as a Woodpecker secret.

- [ ] **Step 7: Run the private contract and policy tests locally**

Run:

```bash
python3 -m venv /tmp/infra-management-infralink-gate
/tmp/infra-management-infralink-gate/bin/pip install pytest pyyaml
/tmp/infra-management-infralink-gate/bin/python -m pytest \
  tests/test_infralink_candidate_contract.py \
  tests/test_woodpecker_infralink_policy.py -q
/tmp/infra-management-infralink-gate/bin/python \
  scripts/verify_infralink_candidate.py --help
```

Expected: tests pass; help contains no credential-bearing argument.

- [ ] **Step 8: Commit the private gate separately**

```bash
git add .woodpecker.yml scripts/verify_infralink_candidate.py \
  tests/test_infralink_candidate_contract.py \
  tests/test_woodpecker_infralink_policy.py
git commit -m "ci: verify exact infralink candidate artifacts"
```

Do not update the Infralink submodule in this commit.

---

### Task 14: Promote Verified Bytes Without Rebuilding

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `security/woodpecker-evidence-cosign.pub`
- Create: `docs/releases/v0.2.0.md`
- Modify: `tests/test_release_manifest.py`
- Modify: `tests/test_release_workflow_policy.py`

- [ ] **Step 1: Add workflow contract tests**

Extend the `yaml.BaseLoader` workflow test before creating `release.yml`:

```python
assert "workflow_dispatch" in candidate["on"]
assert "workflow_dispatch" in release["on"]
assert "python -m build" in candidate_commands
assert "python -m build" not in release_commands
assert "gh release create" in release_commands
```

Also assert the release workflow requires source SHA, candidate artifact ID,
candidate run ID, and a Woodpecker evidence OCI digest. Assert it pulls that
artifact by digest, verifies the Cosign bundle with the committed public key,
fetches full Git history/tags, pushes the annotated tag explicitly, contains
no build or PyPI-upload command, and uses a protected `release` environment.

- [ ] **Step 2: Create the promotion workflow**

`.github/workflows/release.yml` must:

1. accept `source_sha`, `artifact_id`, `candidate_run_id`, and immutable
   `woodpecker_evidence_oci_digest`
2. check out `source_sha` with `fetch-depth: 0`, fetch tags, and verify `HEAD`
3. download the existing candidate artifact by ID
4. query the Actions artifact API by numeric ID, require its name and workflow
   run ID, then verify GitHub attestations for the wheel, sdist,
   `manifest.json`, and `SHA256SUMS` before parsing the manifest and checking
   source SHA, run ID, and version
5. pull the GHCR evidence artifact by digest with ORAS
6. verify `woodpecker-evidence.json` using its Cosign bundle and
   `security/woodpecker-evidence-cosign.pub`
7. require signed pass status and exact equality of source SHA, candidate
   artifact/run IDs, version, and every artifact digest
8. fail if local or remote tag `v0.2.0` exists at another commit
9. in the protected `release` environment run
   `git tag -a v0.2.0 "$source_sha" -m "Infralink v0.2.0"` followed by
   `git push origin refs/tags/v0.2.0`
10. run `gh release create v0.2.0 --verify-tag` and attach the existing wheel,
   sdist,
   `manifest.json`, `SHA256SUMS`, and Woodpecker evidence
11. perform no build command and no PyPI upload

Use environment protection for the final release job so tag creation requires
an explicit approval.

- [ ] **Step 3: Write release and rollback notes**

Create `docs/releases/v0.2.0.md` with:

- envelope version and exit codes
- credential-output removal
- Python compatibility surface
- optional BWS installation and hosted configuration
- hosted-only BWS endpoint constraint and deferred custom transport
- exact candidate source SHA and artifact manifest fields to verify
- private gate requirement
- adoption as a separate submodule commit
- rollback to the previous pinned Infralink revision
- explicit statement that `v0.2.0` performs no deployment or registry mutation

- [ ] **Step 4: Run final repository verification**

Run:

```bash
/tmp/infralink-plan-venv/bin/ruff format --check src tests scripts
/tmp/infralink-plan-venv/bin/ruff check src tests scripts
/tmp/infralink-plan-venv/bin/mypy src
/tmp/infralink-plan-venv/bin/python -m pytest
/tmp/infralink-plan-venv/bin/python scripts/generate_cli_schemas.py
git diff --exit-code -- src/infralink/schemas
/tmp/infralink-plan-venv/bin/python -m build
/tmp/infralink-plan-venv/bin/python -m twine check dist/*
```

Expected: all gates pass and schema generation leaves the tree unchanged.

- [ ] **Step 5: Commit release promotion automation**

```bash
git add .github/workflows/release.yml \
  security/woodpecker-evidence-cosign.pub \
  docs/releases/v0.2.0.md tests/test_release_manifest.py \
  tests/test_release_workflow_policy.py
git commit -m "ci: promote verified infralink release bytes"
```

- [ ] **Step 6: Request code review before any candidate or tag action**

Use `superpowers:requesting-code-review`. The review must confirm:

- all approved spec sections map to passing tests
- no secret value can cross CLI/MCP-ready serialization boundaries
- base install has no Bitwarden dependency
- private gate cannot deploy
- release workflow cannot rebuild
- no tag or release has yet been created

Stop after review. Building the hosted candidate, running the private
Woodpecker gate, creating `v0.2.0`, and updating the consumer submodule are
separate explicitly approved operational actions.
