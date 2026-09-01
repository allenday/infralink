# Fleet Validation

`infralink fleet validate` is the safe operator command for finding invalid
declared fleet topology before a private controller consumes it. It reads an
explicit Infra Registry checkout and reports structural problems. It does not
contact hosts or change any service.

```bash
infralink --registry /srv/infra-registry fleet validate
infralink --registry /srv/infra-registry fleet validate --host relayos-staging
infralink --registry /srv/infra-registry fleet validate --strict
```

## What It Checks

The command reads `hosts/`, the standard edge declaration, and the checkout's
`ansible/services.yml` role/service catalog. It checks active-host identity
uniqueness, role declarations and role dependencies, required role parameters,
declared service relationships, and database-edge authentication naming.

A normal failure still returns an `infralink.cli/v1` success envelope with
`result.valid: false` and exits `1`. Each diagnostic identifies a host or edge
that can be inspected without a repair action.

## What It Does Not Do

This command never reads BWS, environment secrets, databases, Docker, SSH, or
rendered Compose templates. It never renders, reloads, restarts, reconciles, or
otherwise changes a host. Those actions remain private controller concerns.

`--live` reads one controller-produced signed evidence artifact. It does not
contact Prometheus, a host, or a secret service. The local operator config,
not the CLI or MCP request, selects both the artifact and trusted public keys:

```yaml
registry: /srv/infra-registry
fleet_prometheus_evidence:
  artifact_path: /var/lib/infralink-ops/fleet-prometheus-evidence.json
  trusted_public_keys:
    fleet-evidence-v1: BASE64_RAW_ED25519_PUBLIC_KEY
  signing_binding_key_ids:
    infralink-ops/fleet-prometheus-evidence-signing:
      - fleet-evidence-v1
```

`artifact_path` must be absolute. `trusted_public_keys` maps bounded signing
key IDs to base64-encoded raw 32-byte Ed25519 public keys. The reader verifies
the selected checkout's Git revision, the Registry's exact
`operations/observation/fleet-prometheus-targets.yml` target set, the
signature, signed freshness window, and each target outcome.
`signing_binding_key_ids` binds each opaque Registry signing reference to its
allowed key IDs, so a trusted key for another binding is rejected. Missing,
stale, untrusted, incomplete, or provider-failed evidence returns normal
negative diagnostics; it never queries or repairs anything.

## Repair Boundary

Fix declaration diagnostics in the Registry checkout and re-run validation.
After the desired state is valid, the private controller selects and reconciles
the revision through its own release path. Do not use this command as a
deployment trigger.
