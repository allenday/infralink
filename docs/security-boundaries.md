# Security Boundaries

Infralink's public repository must remain safe for contributors, coding agents,
CI systems, and operators. The boundary is simple: public files may describe
interfaces, sanitized examples, and declared references, but they must not expose
private topology, secret values, or live operational authority.

## Public Data

Use sanitized examples in public docs and tests:

- documentation IP ranges such as `192.0.2.0/24`;
- placeholder hostnames such as `database.example.com`;
- UUIDs generated for examples;
- generic service names such as `api`, `postgresql`, and `metrics`.

`tests/test_public_data_boundary.py` scans root docs, all tracked Markdown under
`docs/`, examples, and compatibility docs. Add new public-facing non-Markdown
artifacts to that test when they carry examples or operator guidance.

## Secrets

Topology stores declared references, not values. Safe output may include a
template such as:

```text
postgresql://app:${secret:example/database-password}@192.0.2.10:5432/app
```

Do not add secret values, provider tokens, private secret identifiers, or
resolved credentials to code, docs, examples, test fixtures, logs, or PR bodies.

`infralink secrets inspect` is offline. `infralink secrets audit --provider bws`
requires `infralink[bws]`, `BWS_ACCESS_TOKEN`, and `BWS_ORGANIZATION_ID`; it is
read-only, hosted-BWS-only, limited to declared topology references, and must not
fetch secret values.

## Host Operations

Host bootstrap and apply commands are operationally sensitive. Planning and
status inspection are appropriate for local development. Do not run live apply,
bootstrap `--apply`, SSH mutation, provider mutation, or host reconcile commands
against real infrastructure unless an explicit operator task authorizes it.

Host command outputs must stay bounded and sanitized. Private logs and host
state belong outside this public repository unless converted to deliberate test
fixtures.

## Release Operations

Woodpecker is the sole release executor. Ordinary PR work must not create tags,
GitHub releases, package uploads, Cosign signatures, or deployment artifacts.
The release job is manual, `main`-only, and requires all Python quality gates to
pass first.

Local release scripts are validators and asset assemblers for the protected
workflow. They are not permission to publish from a development checkout.

## Documentation Links

Run the docs checker after Markdown changes:

```bash
.venv/bin/python scripts/check_docs.py
```

The checker validates repository-local Markdown links. External links still need
human review for accuracy and safety.
