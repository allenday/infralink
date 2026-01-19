# Infralink

Infrastructure topology modeling with UUID-based nodes and typed edges.

## Overview

Infralink provides tools for:
- Declaring infrastructure nodes with UUID primary keys
- Defining typed edges between nodes (database, queue, cluster, etc.)
- Resolving edge targets for template rendering
- Health checking edge connectivity
- Generating infrastructure diagrams (Mermaid, D2, Graphviz)
- Generating documentation from topology declarations

## Installation

```bash
# From source
pip install -e .

# With development dependencies
pip install -e ".[dev]"
```

## Quick Start

### Define Your Registry

```yaml
# registry.yml
# - UUID is the primary key (dictionary key)
# - Services are first-class objects with port, protocol, exposure
hosts:
  d1b9e5d5-36b0-459d-a556-96622811fbd5:
    canonical_name: my-database
    status: active
    group: production
    cloud: aws
    tailscale_ip: 100.78.109.111
    services:
      postgresql:
        port: 5432
        protocol: postgresql
        exposure: internal
      redis:
        port: 6379
        protocol: redis
        exposure: internal
      node-exporter:
        port: 9100
        protocol: http
        exposure: internal
```

### Define Your Edges

```yaml
# edges.yml
schema_version: "1.0"
edges:
  - id: app-to-postgres
    type: database
    from:
      hosts:
        - a1b2c3d4-e5f6-7890-abcd-ef1234567890
      service: web-app
    to:
      host: d1b9e5d5-36b0-459d-a556-96622811fbd5
      service: postgresql
      port: 5432
    protocol: postgresql+psycopg2
    metadata:
      criticality: critical
      purpose: Application database
```

### Use the CLI

```bash
# Validate configuration
infralink validate

# Check health of all edges
infralink check

# Check only critical edges
infralink check --critical-only

# Resolve an edge to endpoint
infralink resolve app-to-postgres
# Output: 100.78.109.111:5432

# Generate connection URL
infralink resolve app-to-postgres --format url -u myuser -p mypass -d mydb
# Output: postgresql+psycopg2://myuser:mypass@100.78.109.111:5432/mydb

# Generate diagrams
infralink diagram --format mermaid --output docs/

# Generate documentation
infralink docs --output docs/hosts/
```

### Use the Python API

```python
from infralink import Registry, EdgeSet, EdgeResolver

# Load topology
registry = Registry.load("registry.yml")
edges = EdgeSet.load("edges.yml")

# Query hosts
host = registry.get("my-database")
print(f"Database IP: {host.tailscale_ip}")

# Query edges
db_edges = edges.database_edges()
critical = edges.critical_edges()

# Resolve edges
resolver = EdgeResolver(registry, edges)
endpoint = resolver.get_target_endpoint("app-to-postgres")
url = resolver.get_postgres_url(
    "app-to-postgres",
    user="myuser",
    password="mypass",
    database="mydb"
)

# Health checks
from infralink.health import check_edge_health
result = check_edge_health(edges.get("app-to-postgres"), resolver)
print(f"Healthy: {result.healthy}, Latency: {result.latency_ms}ms")
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `infralink info` | Show registry and edge summary |
| `infralink hosts` | List all hosts |
| `infralink edges-list` | List all edges |
| `infralink validate` | Validate registry and edges |
| `infralink check` | Run health checks on edges |
| `infralink resolve <edge>` | Resolve edge to endpoint |
| `infralink diagram` | Generate infrastructure diagrams |
| `infralink docs` | Generate documentation |

## Edge Types

| Type | Description | Health Check |
|------|-------------|--------------|
| `database` | SQL/NoSQL connections | TCP or Query |
| `queue` | Message broker connections | PING or TCP |
| `cluster` | Multi-node coordination | API |
| `telemetry` | Metrics/logs push | HTTP |
| `monitoring` | Prometheus scrape | HTTP |
| `api` | HTTP API calls | HTTP |
| `storage` | Mount/object storage | TCP |

## Configuration

Default paths (can be overridden with CLI options):
- Registry: `examples/registry.yml`
- Edges: `examples/edges.yml`

```bash
# Custom paths
infralink -r my-registry.yml -e my-edges.yml validate
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy src/

# Linting
ruff check src/
```

## Documentation

- [PRD](PRD.md) - Product Requirements Document
- [BACKLOG](BACKLOG.md) - Product Backlog

## License

MIT
