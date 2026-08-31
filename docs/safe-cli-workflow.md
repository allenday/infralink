# Safe Infralink CLI Workflow

## BLUF

Use `infralink` to inspect and validate declared infrastructure data before a
consumer acts on it. Start with `info`, validate the exact input set, and write
generated diagrams into a new local directory. The public CLI does not select a registry revision, render secrets, or activate services; use the environment's
documented controller workflow for those actions.

## Before You Start

Install the public package and work from a checkout containing the explicit
registry and edge files you want to inspect:

```sh
python -m pip install infralink
cd /path/to/declared-topology
```

Commands below use `registry.yml` and `edges.yml`. Replace both paths together
when inspecting another declared topology. Infralink emits one structured,
redacted result envelope to stdout. Use `--output json` when a script needs
JSON rather than the default YAML output.

## Inspect A Declared Topology

Confirm which source files the command loaded and review the bounded summary
before using a host or edge identifier in a later command:

```sh
infralink --registry registry.yml --edges edges.yml info
infralink --registry registry.yml --edges edges.yml host list --limit 20
infralink --registry registry.yml --edges edges.yml edge list --limit 20
```

Use the opaque cursor returned by a list result to continue a bounded list. Do
not scrape terminal output or assume that an unbounded inventory is safe.

## Validate Before Consuming

Run strict validation before generating a consumer artifact or handing a
topology to another tool. `--check-resolution` verifies each declared edge can
be resolved from the supplied files; `--strict` treats warnings as a negative
result.

```sh
infralink --registry registry.yml --edges edges.yml validate --strict --check-resolution
```

Exit code `0` means the requested validation passed. Exit code `1` is a
completed negative domain result; inspect the envelope diagnostics before
changing inputs. Exit `2` is usage error, while `3` identifies invalid input,
schema, or entity data. See the [CLI contract and exit codes](../README.md#cli-contract)
for the complete boundary.

## Generate A Local Diagram

Generate diagrams only into a deliberate output directory. The command writes
artifacts locally and reports their paths and fingerprints in its result; it
does not modify the declared source files.

```sh
mkdir -p ./artifacts
infralink --registry registry.yml --edges edges.yml diagram --output ./artifacts --format all
```

Use `--group GROUP` to limit output to one declared host group. Add
`--include-terminated` only when the review explicitly needs retired hosts.

## Inspect Release Evidence

Release commands inspect immutable, local evidence handoffs. They do not
publish an image, promote a desired state, or select a host deployment. Supply
the two local evidence files produced by the release workflow:

```sh
infralink release inspect \
  --release-validation ./release-validation.yml \
  --admission ./release-admission.yml
```

For a candidate handoff that has not been published, validate the candidate
first:

```sh
infralink release validate-candidate --candidate ./release-candidate.yml
```

Continue with the exact next action in the returned envelope. Trusted publisher
and environment-controller actions belong to their respective documented
workflows, not to this public CLI.

## When To Stop

Stop and hand off when the task requires any of the following:

- selecting a Registry revision or promotion record;
- resolving or rendering secret values;
- building or publishing a controller image; or
- applying, restarting, or otherwise activating services on a managed host.

Those are private, environment-owned operations. The [architecture boundary](architecture.md#public-and-private-runtime-boundary)
explains the split, and `cyberstorm-dev/infralink-ops` owns the private
host-controller runtime.
