# Local Doctor Agent

`infralink-local-doctor` is a host-local runtime executable. It deliberately
does not extend the `infralink` operator command tree.

## Signed runtime artifact

Both operations require a generated JSON envelope:

```json
{
  "schema_version": "infralink.local-doctor-runtime/v1",
  "config": {
    "canonical_name": "example-node",
    "freshness_seconds": 120,
    "state_path": "/var/lib/infralink/local-doctor/latest.json",
    "metrics_path": "/var/lib/node-exporter/textfile_collector/infralink-doctor.prom",
    "firewall_declaration_path": "/etc/infralink/local-doctor/firewall.json",
    "firewall_allowed_signers_path": "/etc/infralink/local-doctor/firewall.allowed_signers",
    "require_reconcile": true,
    "http_address": "127.0.0.1",
    "http_port": 9473
  },
  "signature": "-----BEGIN SSH SIGNATURE-----..."
}
```

The host supplies the separate, concrete `--allowed-signers` trust root. The
agent verifies the envelope before it reads declared paths or runs checks. The
firewall declaration has its own signed verification boundary.

## Commands

```sh
infralink-local-doctor collect \
  --config /etc/infralink/local-doctor/runtime.json \
  --allowed-signers /etc/infralink/local-doctor/runtime.allowed_signers

infralink-local-doctor serve \
  --config /etc/infralink/local-doctor/runtime.json \
  --allowed-signers /etc/infralink/local-doctor/runtime.allowed_signers
```

`collect` runs only local checks, atomically replaces the persisted JSON result
and Prometheus textfile, then emits one compact JSON envelope. It exits `0` for
a completed collection even when the evidence is unhealthy; malformed or
untrusted runtime input exits `2` with `runtime_config_invalid`.

`serve` performs no collection. It serves the persisted result at
`/v1/doctor/latest` and metrics at `/metrics`, using the signed HTTP binding.
The result endpoint returns `200` only for fresh healthy evidence, otherwise
`503`.
