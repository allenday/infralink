# Infralink - Product Requirements Document

## Overview

**Infralink** is a Python library and CLI tool for modeling infrastructure topology using UUID-based nodes and typed edges. It provides a declarative approach to defining infrastructure connections, enabling automated health checks, diagram generation, and documentation.

## Problem Statement

Modern infrastructure management faces several challenges:

1. **Implicit Dependencies** - Connections between services are often hardcoded in templates, scripts, and configuration files, making them difficult to track and audit.

2. **IP Address Coupling** - Services frequently reference each other via IP addresses, creating brittleness when IPs change.

3. **Documentation Drift** - Infrastructure documentation quickly becomes outdated because it's manually maintained.

4. **Health Check Fragmentation** - Different services have different health check mechanisms, making unified monitoring difficult.

5. **Lack of Visibility** - Understanding the full topology of an infrastructure requires reading multiple files and tracing connections manually.

## Solution

Infralink introduces a **declarative edge-centric model** where:

- **Nodes** are infrastructure hosts identified by UUID (immutable)
- **Edges** are typed connections between nodes with explicit metadata
- **Tooling** consumes these declarations for health checks, diagrams, and docs

## Core Concepts

### Node (Host)

A node represents an infrastructure host with:
- **UUID**: Immutable primary identifier
- **Canonical Name**: Human-readable identifier
- **Network Config**: Tailscale IP, public IP, private IP
- **Services**: List of services running on the host
- **Roles**: Behavioral configurations (e.g., airflow-worker)
- **Metadata**: Cloud provider, group, status, etc.

### Edge

An edge represents a connection between nodes with:
- **ID**: Unique identifier for the edge
- **Type**: database, queue, cluster, telemetry, monitoring, api, storage
- **Source**: Host(s) and service initiating the connection
- **Target**: Host, service, and port receiving the connection
- **Protocol**: Connection protocol (postgresql, redis, http, etc.)
- **Auth**: Authentication configuration
- **Health Check**: How to verify the connection is healthy
- **Metadata**: Purpose, criticality, owner, documentation links

### Edge Types

| Type | Description | Example |
|------|-------------|---------|
| `database` | SQL/NoSQL database connections | PostgreSQL, MariaDB, MongoDB |
| `queue` | Message broker connections | Redis, RabbitMQ, Kafka |
| `cluster` | Multi-node coordination | Elasticsearch transport, Consul gossip |
| `telemetry` | Metrics/logs push | OTLP, Prometheus push |
| `monitoring` | Metrics scrape | Prometheus pull |
| `api` | HTTP API calls | REST, gRPC |
| `storage` | Mount/object storage | NFS, S3, GCS |

## User Personas

### Infrastructure Engineer
- Needs to understand service dependencies
- Wants to know impact of host changes
- Requires health monitoring for all connections

### Platform Team Lead
- Needs visibility into infrastructure topology
- Wants documentation that's always current
- Requires audit trail for compliance

### On-Call Engineer
- Needs quick understanding of what's affected by an outage
- Wants to identify critical paths
- Requires runbook links for incident response

## Features

### P0 - Must Have (MVP)

1. **Registry Loading**
   - Load host definitions from YAML
   - Validate schema with Pydantic
   - Query hosts by UUID, name, group, cloud

2. **Edge Loading**
   - Load edge definitions from YAML
   - Validate edge schema
   - Query edges by type, criticality, target

3. **Edge Resolution**
   - Resolve edge targets to IP:port
   - Generate connection URLs
   - Provide template context for Jinja2

4. **Health Checks**
   - TCP connectivity checks
   - HTTP/HTTPS endpoint checks
   - Redis PING checks
   - Unified result format

5. **CLI Commands**
   - `infralink validate` - Validate registry and edges
   - `infralink check` - Run health checks
   - `infralink resolve <edge>` - Resolve edge to endpoint
   - `infralink hosts` - List hosts
   - `infralink edges` - List edges

### P1 - Should Have

1. **Diagram Generation**
   - Mermaid flowcharts
   - D2 diagrams
   - Graphviz DOT

2. **Documentation Generation**
   - Per-host Markdown pages
   - Index with host listing
   - Edge documentation

3. **Jinja2 Integration**
   - Edge resolver as template filter
   - Template context generation

### P2 - Nice to Have

1. **Watch Mode**
   - Continuous health checking
   - Alerting integration

2. **Diff Detection**
   - Compare registry versions
   - Detect edge changes

3. **Prometheus Metrics**
   - Export edge health as metrics
   - Integration with existing Prometheus

4. **Graph Queries**
   - "What depends on host X?"
   - "Critical path analysis"
   - "Single points of failure"

## Non-Goals

- **Service Discovery** - Infralink is declarative, not dynamic service discovery
- **Deployment** - Does not deploy or configure services
- **Secret Management** - References secrets, doesn't store them
- **Orchestration** - Does not start/stop services

## Technical Requirements

### Dependencies

- Python 3.10+
- PyYAML - YAML parsing
- Click - CLI framework
- Rich - Terminal output
- Pydantic - Schema validation
- Jinja2 - Template integration

### Installation

```bash
pip install infralink

# Or with development dependencies
pip install infralink[dev]
```

### CLI Interface

```bash
# Validation
infralink validate --strict
infralink validate --check-resolution

# Health checks
infralink check
infralink check --critical-only
infralink check --edge airflow-to-postgres
infralink check --type database

# Resolution
infralink resolve airflow-to-postgres
infralink resolve airflow-to-postgres --format url -u airflow -p secret -d airflow

# Diagrams
infralink diagram --format mermaid
infralink diagram --format d2 --output docs/

# Documentation
infralink docs
infralink docs --host relaxgg-bastion
```

### Python API

```python
from infralink import Registry, EdgeSet, EdgeResolver

# Load topology
registry = Registry.load("ansible/inventory/uuid_registry.yml")
edges = EdgeSet.load("ansible/inventory/edges.yml")

# Query
host = registry.get("relaxgg-databases")
db_edges = edges.database_edges()

# Resolve
resolver = EdgeResolver(registry, edges)
endpoint = resolver.get_target_endpoint("airflow-to-postgres")
url = resolver.get_postgres_url("airflow-to-postgres", user="airflow", password="secret", database="airflow")

# Health check
from infralink.health import check_edge_health
result = check_edge_health(edges.get("airflow-to-postgres"), resolver)
```

## Success Metrics

1. **Adoption**: All infrastructure connections declared as edges
2. **Coverage**: Health checks for 100% of critical edges
3. **Freshness**: Documentation generated within 24h of changes
4. **MTTR**: Reduced mean time to resolution via topology visibility

## Milestones

### M1 - Core Library (Week 1-2)
- Registry and Edge loading
- Schema validation
- Edge resolution
- Basic CLI

### M2 - Health Checks (Week 3)
- TCP/HTTP/Redis checks
- CLI integration
- JSON output for automation

### M3 - Generators (Week 4)
- Mermaid diagrams
- Markdown documentation
- CI integration

### M4 - Integration (Week 5-6)
- Jinja2 template filters
- Replace hardcoded IPs in infra-management
- Production deployment

## Open Questions

1. Should edges support multiple protocols (failover)?
2. How to handle dynamic host lists (auto-scaling)?
3. Integration with existing Prometheus alerting?
4. Support for external services (cloud-managed databases)?

## Appendix

### Example Registry

```yaml
hosts:
  relaxgg_databases:
    uuid: d1b9e5d5-36b0-459d-a556-96622811fbd5
    canonical_name: relaxgg-databases
    status: active
    group: relaxgg
    cloud: hetzner-cloud
    tailscale_ip: 100.78.109.111
    services:
      - postgresql
      - redis
      - mariadb
```

### Example Edge

```yaml
edges:
  - id: airflow-to-postgres
    type: database
    from:
      hosts:
        - fa2b9872-d94c-4b20-a73a-57a205560769
      service: airflow-worker
    to:
      host: d1b9e5d5-36b0-459d-a556-96622811fbd5
      service: postgresql
      port: 5432
    protocol: postgresql+psycopg2
    metadata:
      criticality: critical
      purpose: Airflow metadata database
```

---

*Document Version: 1.0*
*Last Updated: 2026-01-19*
