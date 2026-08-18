# RelayOS Observable Reference Model

## Summary

Use `relayos-staging` as the first complete registry adoption of InfraLink's
observable infrastructure model. The migration introduces typed service
instances and component instances for the existing RelayOS deployment while
leaving the proven GitOps deployment contract in place.

The first phase is observation-only. `infra-registry` remains the sole desired
state authority. The current deployment declaration and its rendered Compose
artifacts remain the only execution path until a later controller change
explicitly consumes the typed service model.

## Goals

- Make the actual RelayOS runtime shape queryable as host, service instances,
  components, endpoints, and edges.
- Put readiness, metrics, resource inputs, and dependency contracts on the
  component that owns them.
- Generate Prometheus, Gatus, Grafana, and Doctor projections from the same
  declared component contracts.
- Eliminate the semantic overlap between a host's `services` list and its
  `observability.managed_services` list after compatible projections exist.
- Establish a reusable reference for `relaxgg-services-misc` and later v2
  migrations.

## Non-Goals

- Re-render or recreate RelayOS Compose services from the new model in this
  phase.
- Change containers, images, secrets, ports, firewall policy, or host paths.
- Treat host facts, such as node-exporter or cAdvisor, as application
  components.
- Add a second desired-state selector, a compatibility renderer, or host-local
  observation configuration.

## Reference Topology

```text
relayos-staging host
  host baseline: identity, Tailnet, controller, firewall, node-exporter, cAdvisor
  relayos-edge service
    nginx component
    lego component
    lego-cert-reloader component
  relayos-web service, one instance per tenant
    wordpress component
  relayos-irc-stack service, one instance per tenant
    inspircd component
    anope component
    kiwiirc component
    kiwibnc component
    webircgateway component
  chatflow service
    chatflow component
    chatflow-watchdog component
  edge-prober service
    edge-prober component
```

`relayos-edge`, `relayos-web`, and `relayos-irc-stack` are service profiles.
The three tenant instances are `platform`, `bbchat`, and `bdsmlr`. A profile
defines reusable component slots, endpoint contracts, resource slots, and
dependency templates. It is not itself a runtime observable.

The edge service is shared by the tenant services. Its NGINX component owns
the public HTTP and HTTPS endpoints. Lego and the certificate reloader are
components because they have independent lifecycle and resource requirements,
but neither is independently presented as an application service.

## Components, Resources, And Endpoints

Each component declaration must include:

- a stable component instance identity scoped to its host and service instance;
- the profile component slot it realizes;
- required `config`, `secret`, and `storage` resource bindings;
- named endpoints it owns, including protocol and address/port binding;
- local readiness checks and required metric signals; and
- required outgoing dependency edges.

The existing `/opt/services/data` paths are `storage` resources. Rendered
configuration and BWS references are `config` and `secret` resources. They are
inputs to a component and must not become topology nodes.

The actual `edge-prober` endpoint is port `9119`. The existing catalog's
`9115` declaration is stale and must be corrected as part of this migration;
the typed declaration is the source of truth for every projection.

## Metric And Health Model

Metrics belong to components, not to host-level service lists.

- NGINX exposes availability through its public listener endpoints. If VTS is
  enabled, the exporter is a separate component with a metrics endpoint.
- Each WordPress component exposes HTTP availability through its tenant's
  public endpoint.
- Inspircd owns plaintext and TLS IRC listener endpoints. Anope, KiwiIRC,
  KiwiBNC, and WebIRC gateway expose their own local or public capabilities as
  appropriate.
- The edge-prober component exposes its probe endpoint and emits probe metrics
  for its target edges. Those target metrics are edge observation, not NGINX or
  WordPress application metrics.
- Node exporter and cAdvisor remain host-baseline telemetry sources. Their
  scrape contracts contribute to host readiness; they do not imply application
  readiness.

One declared component metric contract projects to Prometheus scrape targets
and labels, Gatus availability checks where appropriate, Grafana panels, and
Doctor evidence. Gatus must not be an alternative source of endpoint intent.

## Dependencies And Readiness

Edges connect named component endpoints. Examples include:

- a tenant WordPress component to its MariaDB and Redis endpoints;
- Anope to its tenant Inspircd transport endpoint and MariaDB endpoint;
- KiwiIRC and KiwiBNC to the relevant IRC endpoints;
- NGINX to the tenant HTTP backends; and
- edge-prober to the endpoints it is expected to observe.

Readiness rolls up from required resources, local checks, metric signals, and
outgoing edges to component, then service instance, then host. A failed
WordPress-to-MariaDB edge affects that WordPress source component and its
service instance, not unrelated tenants or the entire host by association.

## Registry And Projection Boundary

The durable declarations belong in `infra-registry` alongside the existing
host manifest and service-profile catalog. The controller, Gatus generator,
Prometheus generator, Grafana renderer, and Doctor consume the same typed
declarations. `/var/lib/infralink/registry`, `/opt/services`, generated
monitoring files, and runtime observations remain cache, rendered output, or
evidence only.

Phase one does not change the current RelayOS deployment declaration. It adds
the typed observation model and validates that projections match the existing
live contracts. A later explicit migration can make the deployment renderer
consume the typed component/resource bindings, then delete superseded host
lists and verification duplication in one reviewed change.

## Acceptance Criteria

1. RelayOS has typed instances for the services and components above, with no
   ambiguous host-level application entries.
2. All endpoint, resource, and dependency identities are stable and host
   scoped.
3. The generated Prometheus, Gatus, Grafana, and Doctor plans agree on the
   component and edge identities.
4. The catalog declares edge-prober port `9119`, and the rendered plan matches
   the live listener.
5. Existing Compose deployment and post-deploy verification remain unchanged
   during phase one.
6. Tests prove the projection outputs and reject an unbound required resource,
   missing component endpoint, or disagreement between a declared listener and
   its observation contract.

## Rollout

1. Add typed RelayOS profiles and instances without changing deployment
   rendering.
2. Add projection tests and compare generated plans with the current live
   endpoint contracts.
3. Enable the generated observations on RelayOS staging and verify Doctor,
   Prometheus, Gatus, and Grafana agree.
4. Use the same model for `relaxgg-services-misc`.
5. Only after both reference hosts are stable, plan the separate controller
   migration that makes component resources deployable and removes legacy
   duplicate lists.
