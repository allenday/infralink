# V2 Diagram Projection

`infralink topology diagram` renders declared
`infralink.observation/v2` topology as bounded inline Mermaid or Graphviz DOT
source. It is the read-only diagram path for agents and operators. The sibling
`infralink diagram --output ...` command writes legacy Registry-derived
artifacts.

```bash
infralink topology diagram --source ./observation
infralink topology diagram --source ./observation --scope host --host HOST_UUID
infralink topology diagram --source ./observation --scope service \
  --service HOST_UUID/SERVICE_INSTANCE_ID --syntax dot
```

The command defaults to the standard YAML `infralink.cli/v1` envelope. Pass
global `--output json` for JSON. The graph source is returned in
`result.source`; no output directory or artifact is created.

## Inputs

`--source` is required and may be supplied more than once. Every selected
document must use `infralink.observation/v2`.

| Scope | Required selector | Result focus |
| --- | --- | --- |
| `full` | none | every declared endpoint and edge |
| `host` | `--host HOST_UUID` | that host plus direct edge neighbours |
| `service` | `--service HOST_UUID/SERVICE_INSTANCE_ID` | that service plus direct edge neighbours |

The command rejects any other selector combination. It accepts no
`--registry`, `--edges`, provider, controller, secret, network, repair, or
output-path input.

## Result And Safety

The typed result contains the selected `syntax`, `scope`, resolved focus,
node and edge counts, and the bounded source string. Rendering uses the shared
V2 topology projection and `render_v2_mermaid` or `render_v2_dot`; it never
uses legacy generators or writes files.

Native MCP exposes the equivalent `infralink_topology_diagram` tool. Its only
inputs are `source`, `scope`, `host`, `service`, and `syntax`; it returns the
same typed envelope as the CLI.
