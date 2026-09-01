# Release Package Version Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent an Infralink GitHub release from publishing assets whose wheel metadata disagrees with its requested release version.

**Architecture:** Keep protected `main` as the release authority. The release helper will inspect the built wheel metadata, and the existing Woodpecker release step will call that helper before checksums, signing, tags, or GitHub-release creation.

**Tech Stack:** Python 3.12, `zipfile`, Woodpecker, Hatchling, pytest.

---

### Task 1: Validate wheel package metadata

**Files:**
- Modify: `scripts/release.py`
- Modify: `tests/test_release.py`

- [x] **Step 1: Write a failing test**

```python
def test_validate_distribution_version_rejects_wheel_metadata_mismatch(tmp_path: Path) -> None:
    module = load_module()
    wheel = tmp_path / "infralink-0.6.10-py3-none-any.whl"
    write_wheel_metadata(wheel, "0.6.9")

    with pytest.raises(module.ReleaseError, match="wheel metadata version"):
        module.validate_distribution_version(dist=tmp_path, version="0.6.10")
```

- [x] **Step 2: Verify the test fails because the helper is absent**

Run: `pytest -q -o addopts='' tests/test_release.py::test_validate_distribution_version_rejects_wheel_metadata_mismatch`

- [x] **Step 3: Implement the minimal helper and CLI command**

Read the sole `*.dist-info/METADATA` member from the exact requested wheel and reject a missing or mismatched `Version:` field.

- [x] **Step 4: Verify the release tests pass**

Run: `pytest -q -o addopts='' tests/test_release.py`

### Task 2: Enforce validation before release side effects

**Files:**
- Modify: `.woodpecker.yml`

- [x] **Step 1: Invoke the new helper immediately after `python -m build`**

```sh
python scripts/release.py distribution-version --dist dist --version "$${RELEASE_VERSION}"
```

- [x] **Step 2: Run formatting, static analysis, package build, and full tests**

Run: `ruff check src tests scripts && mypy src scripts && pytest -q -o addopts='' && python -m build`

### Task 3: Prepare the corrected public release

**Files:**
- Modify: `src/infralink/__about__.py`
- Create: `docs/releases/v0.6.10.md`

- [x] **Step 1: Set the source package version to `0.6.10`**
- [x] **Step 2: Add concise release notes for the portable firewall declaration and artifact-version invariant**
- [x] **Step 3: Build and inspect the wheel metadata**

Run: `python -m build && python scripts/release.py distribution-version --dist dist --version 0.6.10`

- [x] **Step 4: Commit and open a PR linked to #238**
