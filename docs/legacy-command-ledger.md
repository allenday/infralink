# Legacy Command Disposition Ledger

This is the execution inventory for [Infralink #296](https://github.com/cyberstorm-dev/infralink/issues/296). The source tables cover every non-test path under `infra-management/scripts/` at its audited `main` revision. The separate deployed-command table records runtime executables, not source paths in that tree. Registry remains sole desired-state authority. A row records a disposition; it does not authorize retention or a second deployment path.

`migrate` requires the listed replacement and acceptance gate. `private` means a private Ops module only. `retire` is the default. `external` means relocate to the owning application, not Infralink.

## Deployed Commands (Runtime Inventory)

These are deployed executable names or locations evidenced by the active
runtime/package configuration. They are outside the `infra-management/scripts/`
source-tree audit.

| Source | Owner/caller; mutation/authority | Disposition; issue; removal gate | Acceptance |
| --- | --- | --- | --- |
| `infralink-local-doctor` | Infralink local diagnostics; read-only | **migrate** to `infralink doctor`; #270 | CLI/MCP parity |
| `/usr/local/sbin/infralink-host` | Historical host launcher; no longer a deployed public command | **retire**; private systemd runtime installed by Ops #278 and proven through Ops #290/#293 | MTA Registry revision -> timer -> reconcile evidence -> live SMTP endpoint |
| `infralink-controller-*` | Controller-only package helpers | **private** modules; Ops #232 closed after the MTA normal-reconcile proof | No console scripts/callers |

## Compatibility And Controller Sources

| Source | Owner/caller; mutation/authority | Disposition; issue; removal gate | Acceptance |
| --- | --- | --- | --- |
| `__init__.py` | Legacy package marker; none | **retire**; #295 | No legacy imports |
| `bootstrap-gitops.sh` | Bastion bootstrap; remote mutation | **migrate** `infralink host bootstrap`; Ops #278 | Bootstrap proof |
| `doctor_host.sh` | Legacy host repair; host mutation | **migrate** doctor/bootstrap; Ops #278 | Readiness evidence |
| `self-deploy.sh` | Legacy cron deploy; legacy checkout | **migrate** private Ops timer; Ops #10/#278 | Registry -> live proof |
| `self_deploy_steps/fix_routing.sh` | Legacy self-deploy; host routing mutation | **migrate** Ops firewall/SNAT; Ops #10 | Typed live policy |
| `self_deploy_steps/wordpress.sh` | Legacy app hook; application mutation | **retire**; #295 | No caller/config moved to Registry |
| `render_compose.py` | Legacy renderer; artifact render | **private** Ops template renderer; Ops #10 | Runtime render proof |
| `jinja_helpers.py` | Legacy renderer helper; none alone | **private** Ops helper; Ops #10 | No executable caller |
| `infralink-controller-reconcile` | Legacy controller entrypoint; applies state | **private** Ops runtime; Ops #10 | Normal reconcile proof |
| `infralink-controller-preflight.py` | Legacy preflight; read-only | **private** Ops doctor; Ops #10 | Typed preflight evidence |
| `infralink-controller-preservation.py` | Legacy preservation; deployment action | **private** Ops transition module; Ops #10 | No-recreate proof |
| `infralink-controller-artifacts.py` | Legacy artifact materializer; writes files | **private** Ops materializer; Ops #17 | Registry -> consumer proof |
| `self-deploy-v2-contract-requirements.lock` | Alternate payload lock; none | **retire**; Ops #10 | No installer references it |
| `self-deploy-v2-preservation-bootstrap.py` | Alternate runtime creator; host mutation | **retire**; Ops #10 | One controller path |
| `self-deploy-v2-preservation-shadow.py` | Alternate runtime; applies state | **retire**; Ops #10 | One controller path |
| `self-deploy-v2-preserve.sh` | Alternate cron wrapper; applies state | **retire**; Ops #10 | One controller path |
| `self-deploy-v2-runtime-payload.py` | Alternate payload packager; writes state | **retire**; Ops #10 | No payload caller |
| `self-deploy-v2-shadow.sh` | Alternate cron wrapper; applies state | **retire**; Ops #10 | One controller path |
| `release-admission-runtime-payload.py` | Historical release runtime; alternate selector | **retire**; #295 | No caller |
| `release-admission-runtime-requirements.lock` | Historical dependency lock; none | **retire**; #295 | No installer |
| `release-admission-shadow.py` | Historical release runtime; alternate selector | **retire**; #295 | No caller |
| `render_core_release_evidence.py` | CI release evidence; no state authority | **migrate** public evidence contract; #270 | Canonical evidence operation |

## Observation And Validation Sources

| Source | Owner/caller; mutation/authority | Disposition; issue; removal gate | Acceptance |
| --- | --- | --- | --- |
| `fleet_validate.py` | CI/operator legacy topology read | **migrate** `infralink fleet validate`; #270 | Registry fixture + CLI/MCP parity |
| `validate_roles.py` | CI/self-deploy legacy validation | **migrate** `infralink fleet validate`; #270 | Secret checks separately owned |
| `fleet_health.py` | Monitoring operator; Prometheus query | **migrate** signed evidence; Ops #257 | Producer evidence |
| `check_prom_freshness.py` | Monitoring operator; Prometheus query | **migrate** signed evidence; Ops #257 | Freshness evidence |
| `prometheus_qa.py` | Monitoring CI/operator; Prometheus query | **migrate** signed evidence; Ops #257 | No legacy caller |
| `audit_runner.sh` | Legacy audit cron; writes local files | **retire**; Ops #257 | Evidence artifact live |
| `run_audits.sh` | Legacy audit cron; runs local tools | **retire**; Ops #257 | No timer/caller |
| `render_status_matrix.py` | Legacy audit UI; writes static state | **retire**; Ops #257 | Registry-derived Grafana/Gatus |
| `generate_blackbox_targets.py` | Prometheus generator; writes config | **migrate** direct projection; Ops #2 | Registry -> target live |
| `generate_prometheus_config_auth.py` | Prometheus generator; credentials/config | **migrate** direct projection; Ops #2 | Registry -> Prometheus live |
| `generate_gatus_config.py` | Gatus generator; credentials/config | **migrate** direct projection; Ops #2 | Registry -> Gatus live |
| `generate_edge_prober_config.py` | Edge-prober generator; config | **migrate** direct projection; Ops #2 | Registry -> prober config |
| `refresh_monitoring.sh` | Legacy deploy hook; runtime mutation | **migrate** observation renderer; Ops #2 | Watchtower reconcile proof |
| `infra-observe` | Legacy CLI/CI plan renderer | **retire** after direct projection; Ops #2 | No plan/binding input |
| `gatus-observation-canary` | One-shot Watchtower transition | **retire**; Ops #2 | Gatus direct projection live |
| `grafana-datasource-canary` | One-shot Watchtower transition | **retire**; Ops #2 | Datasource direct projection live |
| `prometheus-observation-canary` | One-shot Watchtower transition | **retire**; Ops #2 | Prometheus direct projection live |
| `observation-quality-requirements.lock` | Legacy observation lock | **retire**; Ops #2 | No CI caller |

## Provider, Firewall, And App Helpers

| Source | Owner/caller; mutation/authority | Disposition; issue; removal gate | Acceptance |
| --- | --- | --- | --- |
| `bws.py` | Legacy BWS operator wrapper; provider mutation | **retire** public control-plane use; #295 | No deploy caller |
| `validate_bws_secrets.py` | CI/operator BWS validation | **migrate** typed secret declarations; #270 | No secret-value output |
| `sync_dkim_from_bws.py` | Legacy mail deploy; writes key files | **migrate** config-tree artifact; Ops #17 | Registry -> consumer proof |
| `firewall_contract.py` | Legacy deploy/CI firewall action | **migrate** Infralink firewall + Ops renderer; #270/Ops #203 | Typed nft live proof |
| `register_hosts.sh` | Legacy inventory authoring; writes state | **migrate** `infralink host create`; #146 | Git authoring checkout only |
| `list_mutable_compose_services.py` | Legacy controller preflight; read-only | **private** Ops transitions; Ops #10 | No executable caller |
| `check_email.py` | Manual Gmail helper; external mail mutation | **external**; #295 | Relocated to app owner |
| `export_grafana_dashboards.py` | Manual Grafana export; writes Git tree | **retire**; Ops #2 | Registry-owned dashboards |
| `run_openclaw_browser.sh` | Personal local helper; tunnel/browser | **external**; #295 | Relocated to personal tooling |
| `sync-static-sites.sh` | Static-site host helper; Git worktree mutation | **external**; #295 | Relocated to service owner |

## Core One-Shot Runners

| Source | Owner/caller; mutation/authority | Disposition; issue; removal gate | Acceptance |
| --- | --- | --- | --- |
| `watchtower-converge.sh` | Historical Woodpecker deploy; second apply path | **retire**; Ops #10 | Watchtower timer proof |
| `watchtower-postcheck.sh` | Historical core postcheck; read-only | **migrate** `infralink doctor host`; Ops #278 | Target evidence |
| `citadel-converge.sh` | Historical Woodpecker deploy; second apply path | **retire**; Ops #10 | Citadel timer proof |
| `citadel-postcheck.sh` | Historical core postcheck; read-only | **migrate** `infralink doctor host`; Ops #278 | Target evidence |

No source may be deleted merely because a replacement exists. Deployment rows require `Registry revision -> self-deploy fetch -> rendered/service change -> live evidence`; no row permits a compatibility deploy path, persisted plan, or local desired-state selector.
