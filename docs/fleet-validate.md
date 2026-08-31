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

`--live` is intentionally fail-closed in the current release. It adds a
`live_evidence_unavailable` capability-gap diagnostic rather than pretending to
perform a health check. A future bounded read-only evidence provider must be
introduced before that mode can report live fleet evidence.

## Repair Boundary

Fix declaration diagnostics in the Registry checkout and re-run validation.
After the desired state is valid, the private controller selects and reconciles
the revision through its own release path. Do not use this command as a
deployment trigger.
