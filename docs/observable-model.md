# Observable Model

Infralink models declared infrastructure as a composition of observable
instances. The model is the public contract for registry authors, renderers,
operator tools, and observability projections.

`infra-registry` is the sole desired-state authority. Infralink defines the
typed model and validates, renders, and inspects it; it does not become another
source of desired state.

## Topology

The runtime hierarchy is:

```text
Host
  Service instance
    Component instance
      Endpoint
        Edge
```

- A **host** is a machine instance. It owns baseline evidence such as identity,
  Tailnet reachability, controller state, firewall posture, and host metrics.
- A **service instance** is a profile instantiated on a host. It owns the
  readiness contract for its required components and service-level edges.
- A **component instance** is a concrete process or container within a service
  instance. A single-container service has one component; a proxied
  Elasticsearch service can have `elasticsearch` and `nginx` components.
- An **endpoint** is a named addressable capability of a component: for
  example, `http`, `metrics`, or `transport`.
- An **edge** connects source and target endpoints. It describes a required
  dependency, not an incidental network path.

Hosts, service instances, component instances, and edges are observables: each
has declared readiness requirements, evidence, status, and stable identity.
`ServiceProfile` is a template, not a runtime observable; it defines component
slots, resource slots, endpoint contracts, and dependency templates reused by
service instances.

An edge is **intra-service** when both endpoint owners are components of the
same service instance. It is **inter-service** otherwise. This distinction is
derived from the endpoints; registry authors do not duplicate it as an
independent field.

Host facts must not be represented as pseudo-components merely to fit an edge.
If a host-level capability needs topology treatment, model it as an explicit
normal service with a component and endpoint.

## Resources

Components consume declared resource slots. A profile defines the required
slots and their constraints; the registry binds deployment-specific values.

The initial resource kinds are:

- `config`: rendered configuration such as an Nginx virtual host or Prometheus
  scrape configuration;
- `secret`: a named secret reference, resolved only by an approved provider;
- `storage`: a named volume or host path with its ownership and mode contract.

Resources are component inputs. They are not topology nodes and do not need a
global resource graph. A component's readiness includes required resources
being rendered, validated, and available at its declared mount or path.

## Metrics And Evidence

A component profile declares typed metric signals when it exposes metrics. A
signal identifies its producer endpoint, scrape path, stable metric name, unit,
allowed labels, optional health threshold or query, and whether it is required
for readiness. The registry binds concrete addresses and labels at
instantiation.

The same declared contract is projected into the operational systems:

```text
component metric contract
  -> Prometheus scrape target and labels
  -> Gatus availability or health check
  -> Grafana dashboard panels
  -> Doctor evidence
```

Gatus validates availability and declared health-relevant signals. Prometheus
collects raw metrics. Grafana visualizes those metrics through reusable
dashboard templates. Doctor combines declared requirements and live evidence;
it does not invent a second desired state or replace host telemetry.

Edges may also have observer-produced metrics such as connection success,
latency, or certificate expiry. These metrics belong to the edge observation
contract, not to either endpoint's application metric contract.

## Readiness Rollup

Readiness rolls up through declared required relationships:

1. A component is ready when its required resources, metrics, local checks,
   and required outgoing edges are ready.
2. A service instance is ready when all of its required components and
   service-level required edges are ready.
3. A host is ready when its baseline requirements and required service
   instances are ready.

An edge failure affects the declared dependency source. It must not make every
related host or service unhealthy by association.

This shape supports both operational views without special cases:

```text
doctor host <host>
  host -> services -> components -> required edges

doctor component <component name>
  component profile -> every instantiated component -> readiness
```

The latter is intentionally a component query. `doctor service <profile>`
remains a service-profile query so a standalone `nginx` service is never
ambiguous with an `nginx` component inside another service.

## Projection Boundary

Registry declarations express enduring operator intent: topology, profiles,
resources, metric contracts, dashboard/view selection, and readiness policy.
Rendered Compose, Prometheus targets, Gatus endpoints, Grafana dashboards,
Doctor plans, and runtime evidence are derived artifacts. They must never
override or silently preserve registry desired state.

Renderers must be generic. They may not contain Citadel, Watchtower, or other
deployment-specific branching. Adding a host or service instance should
materialize the applicable generic checks and views from its profile and
contracts.

## Backfill Rule

This is the authoritative intended model, including where existing generated
artifacts or legacy observation documents have not yet reached it. New work
must conform to it. Existing exceptions are backfill debt: remove the exception
or migrate it to the model rather than documenting the exception as an
alternative architecture.
