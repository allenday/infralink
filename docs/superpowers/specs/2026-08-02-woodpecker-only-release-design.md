# Woodpecker-Only Release Design

## Goal

Make Woodpecker the only Infralink CI and release executor. GitHub remains the
canonical public source repository and the destination for tags and release
assets, but GitHub Actions is not part of the build or trust chain.

## Boundaries

- Delete every workflow under `.github/workflows/`.
- Keep the existing three-version Woodpecker quality matrix authoritative.
- Add one manual, `main`-only release step that runs once on Python 3.12.
- Require the requested release version to equal the package version and
  require the pipeline commit to equal protected `main`.
- Rebuild release files inside the release step after quality passes.
- Publish the wheel, sdist, `SHA256SUMS`, and a passwordless Cosign bundle to a
  GitHub release tagged `v0.2.0`.
- Use checksum-pinned `gh` and Cosign binaries.
- Fail if the tag or release already exists. Recovery automation is deferred;
  an operator must inspect partial remote state before retrying.

## Secrets

The manual release step receives only:

- `infralink_release_github_token`: temporary contents-write GitHub token.
- `infralink_gate_cosign_private_key`: passwordless Cosign private key whose
  public key is already committed.

Both are manual-event-only Woodpecker repository secrets. Moving the GitHub
credential off the personal `allenday` identity remains tracked in issue #22.

## Private Compatibility

The infra-management private compatibility experiment is not a release
dependency. Its GitHub Actions artifact/run/attestation and GHCR evidence
publisher are removed. A later, optional Woodpecker diagnostic may build an
exact public source SHA and run it against private registry data without
controlling tags or releases.

## Verification

Policy tests require no GitHub workflow files, exactly one release-capable
Woodpecker step, main/manual/source/version binding, isolated release secrets,
checksum-pinned tools, and the exact release asset set. The normal Python
quality suite and Woodpecker branch/PR matrices must pass before merge.
