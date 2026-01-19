# Infralink - Product Backlog

## Epic 1: Core Library

### 1.1 Registry Management

- [x] **IL-001**: Define Host Pydantic schema
- [x] **IL-002**: Implement Registry.load() from YAML
- [x] **IL-003**: Implement host query methods (by_uuid, by_name, filter)
- [x] **IL-004**: Add host status enum (active, terminated, provisioning)
- [ ] **IL-005**: Support registry includes (split large registries)
- [ ] **IL-006**: Add registry diff (compare two versions)
- [ ] **IL-007**: Support host aliases

### 1.2 Edge Management

- [x] **IL-010**: Define Edge Pydantic schema
- [x] **IL-011**: Implement EdgeSet.load() from YAML
- [x] **IL-012**: Implement edge type enum (database, queue, etc.)
- [x] **IL-013**: Add edge query methods (by_type, by_criticality)
- [x] **IL-014**: Support selector-based source matching
- [ ] **IL-015**: Support wildcard targets (monitoring edges)
- [ ] **IL-016**: Add edge validation (cyclic detection)
- [ ] **IL-017**: Support edge groups/bundles

### 1.3 Edge Resolution

- [x] **IL-020**: Implement EdgeResolver class
- [x] **IL-021**: Resolve edge to IP:port
- [x] **IL-022**: Generate connection URLs (PostgreSQL, MySQL, Redis)
- [x] **IL-023**: Provide template context dictionary
- [ ] **IL-024**: Support multiple IP preferences (tailscale > public > private)
- [ ] **IL-025**: Add URL parameter escaping
- [ ] **IL-026**: Support connection string templates

---

## Epic 2: Health Checks

### 2.1 Check Implementations

- [x] **IL-030**: Implement TCP connectivity check
- [x] **IL-031**: Implement HTTP/HTTPS endpoint check
- [x] **IL-032**: Implement Redis PING check
- [ ] **IL-033**: Implement PostgreSQL query check
- [ ] **IL-034**: Implement MySQL query check
- [ ] **IL-035**: Implement Elasticsearch cluster health check
- [ ] **IL-036**: Implement certificate expiry check

### 2.2 Check Framework

- [x] **IL-040**: Define HealthCheckResult dataclass
- [x] **IL-041**: Implement check_edge_health() function
- [ ] **IL-042**: Add timeout configuration
- [ ] **IL-043**: Support retry logic
- [ ] **IL-044**: Add check parallelization
- [ ] **IL-045**: Implement check caching (avoid rapid rechecks)

### 2.3 Alerting Integration

- [ ] **IL-050**: Output Prometheus metrics format
- [ ] **IL-051**: Integration with Alertmanager
- [ ] **IL-052**: Slack notification support
- [ ] **IL-053**: PagerDuty integration

---

## Epic 3: CLI

### 3.1 Core Commands

- [x] **IL-060**: Implement `infralink` main CLI group
- [x] **IL-061**: Implement `infralink validate` command
- [x] **IL-062**: Implement `infralink check` command
- [x] **IL-063**: Implement `infralink resolve` command
- [x] **IL-064**: Implement `infralink hosts` command
- [x] **IL-065**: Implement `infralink edges` (edges-list) command
- [x] **IL-066**: Implement `infralink info` command

### 3.2 Generator Commands

- [x] **IL-070**: Implement `infralink diagram` command
- [x] **IL-071**: Implement `infralink docs` command
- [ ] **IL-072**: Add `--watch` mode for continuous operation
- [ ] **IL-073**: Add `--output-format json` for all commands

### 3.3 UX Improvements

- [ ] **IL-080**: Add shell completion (bash, zsh, fish)
- [ ] **IL-081**: Add `--config` for configuration file
- [ ] **IL-082**: Add progress bars for long operations
- [ ] **IL-083**: Improve error messages with suggestions

---

## Epic 4: Generators

### 4.1 Diagram Generators

- [x] **IL-090**: Implement Mermaid flowchart generator
- [x] **IL-091**: Implement D2 diagram generator
- [x] **IL-092**: Implement Graphviz DOT generator
- [ ] **IL-093**: Add PlantUML support
- [ ] **IL-094**: Add filtering by group/cloud
- [ ] **IL-095**: Support custom styling/themes

### 4.2 Documentation Generators

- [x] **IL-100**: Implement per-host Markdown generator
- [x] **IL-101**: Implement host index generator
- [x] **IL-102**: Implement edge index generator
- [ ] **IL-103**: Add per-edge documentation
- [ ] **IL-104**: Add group-level documentation
- [ ] **IL-105**: Support MkDocs integration
- [ ] **IL-106**: Add changelog generation (from git history)

### 4.3 Config Generators

- [ ] **IL-110**: Generate Prometheus scrape config from edges
- [ ] **IL-111**: Generate Blackbox exporter targets
- [ ] **IL-112**: Generate Grafana datasources

---

## Epic 5: Integration

### 5.1 Jinja2 Integration

- [ ] **IL-120**: Create Jinja2 extension for edge resolution
- [ ] **IL-121**: Add `edge()` filter
- [ ] **IL-122**: Add `edge_url()` filter
- [ ] **IL-123**: Add `edge_hosts()` filter
- [ ] **IL-124**: Document Jinja2 usage

### 5.2 Infra-Management Integration

- [ ] **IL-130**: Migrate airflow-worker template to use edges
- [ ] **IL-131**: Migrate n8n template to use edges
- [ ] **IL-132**: Migrate Grafana datasources to use edges
- [ ] **IL-133**: Add infralink to CI/CD pipeline
- [ ] **IL-134**: Generate Prometheus config from edges

### 5.3 API/Library

- [ ] **IL-140**: Document Python API
- [ ] **IL-141**: Add type stubs for IDE support
- [ ] **IL-142**: Publish to PyPI
- [ ] **IL-143**: Create GitHub Actions workflow

---

## Epic 6: Advanced Features

### 6.1 Graph Analysis

- [ ] **IL-150**: Implement "what depends on X?" query
- [ ] **IL-151**: Implement critical path analysis
- [ ] **IL-152**: Detect single points of failure
- [ ] **IL-153**: Calculate blast radius for host failure
- [ ] **IL-154**: Identify isolated hosts (no edges)

### 6.2 Change Detection

- [ ] **IL-160**: Detect new edges (added)
- [ ] **IL-161**: Detect removed edges
- [ ] **IL-162**: Detect edge modifications
- [ ] **IL-163**: Generate change report

### 6.3 External Services

- [ ] **IL-170**: Support cloud-managed databases (CloudSQL, RDS)
- [ ] **IL-171**: Support SaaS endpoints
- [ ] **IL-172**: Support external IP targets
- [ ] **IL-173**: Add external service health checks

---

## Backlog Prioritization

### Sprint 1 (Current)
- IL-001 through IL-023 (Core library) ✓
- IL-030, IL-031, IL-032 (Basic health checks) ✓
- IL-060 through IL-066 (Core CLI) ✓

### Sprint 2
- IL-070, IL-071 (Generator commands) ✓
- IL-090 through IL-102 (Generators) ✓
- IL-033 through IL-035 (More health checks)

### Sprint 3
- IL-120 through IL-124 (Jinja2 integration)
- IL-130 through IL-132 (Infra-management migration)

### Sprint 4
- IL-150 through IL-154 (Graph analysis)
- IL-140 through IL-143 (Publication)

---

## Technical Debt

- [ ] **TD-001**: Add comprehensive unit tests
- [ ] **TD-002**: Add integration tests
- [ ] **TD-003**: Improve error handling consistency
- [ ] **TD-004**: Add logging throughout
- [ ] **TD-005**: Performance optimization for large registries
- [ ] **TD-006**: Documentation improvements

---

*Last Updated: 2026-01-19*
