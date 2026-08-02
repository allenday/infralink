# Woodpecker-Only Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove GitHub Actions and release Infralink v0.2.0 through one protected Woodpecker manual pipeline.

**Architecture:** Extend the existing Woodpecker quality matrix with one Python 3.12 manual release step. The step builds and signs exact assets, then uses the GitHub Releases API only as the publication destination.

**Tech Stack:** Woodpecker CI, Python build/twine, Cosign, GitHub CLI, pytest.

---

### Task 1: Remove GitHub Actions Policy

**Files:**
- Delete: `.github/workflows/ci.yml`
- Delete: `.github/workflows/release-candidate.yml`
- Delete: `.github/workflows/release.yml`
- Modify: `tests/test_release_workflow_policy.py`

- [ ] Replace Actions-specific policy tests with assertions that no workflow files exist and Woodpecker is authoritative.
- [ ] Run the focused policy test and confirm it fails before workflow deletion.
- [ ] Delete the three workflows and confirm the focused test passes.

### Task 2: Add Manual Woodpecker Release

**Files:**
- Modify: `.woodpecker.yml`
- Create: `scripts/release_v0_2.py`
- Create: `tests/test_woodpecker_release_policy.py`
- Create: `tests/test_release_v0_2.py`

- [ ] Add failing tests for manual/main/Python 3.12 selection, exact SHA and version validation, secret isolation, pinned tools, exact assets, and fail-closed pre-existing release state.
- [ ] Implement a stdlib release helper for input validation, deterministic checksums, and exact asset discovery.
- [ ] Add the single release step after `quality`, using checksum-verified `gh` and Cosign binaries.
- [ ] Run release, policy, packaging, Ruff, and mypy tests.

### Task 3: Remove Obsolete Promotion Code and Documentation

**Files:**
- Delete: `scripts/verify_release_promotion.py`
- Delete: `tests/test_release_promotion.py`
- Modify: `PRD.md`
- Modify: `docs/releases/v0.2.0.md`
- Modify: `docs/compatibility/v0.2.md`

- [ ] Remove Actions artifact/run/attestation instructions and promotion verifier references.
- [ ] Document Woodpecker pipeline identity, signed checksum bundle, and GitHub release assets.
- [ ] Run repository-wide reference scans and the complete Infralink test suite.

### Task 4: Remove infra-management Actions Gate

**Files:**
- Modify: `infra-management/.woodpecker.yml`
- Delete or narrow: Actions-specific candidate verification, publication, and policy tests/scripts identified by reference scan.

- [ ] Remove manual steps that consume GitHub Actions artifact/run IDs or publish release-gating evidence.
- [ ] Remove the associated Actions-read and GHCR-evidence secrets from documentation and Woodpecker.
- [ ] Preserve ordinary deployment and self-deploy validation steps unchanged.
- [ ] Run focused Woodpecker policy tests and branch/PR CI before merge.
