"""Read-only SSH transport for host readiness collection."""

from __future__ import annotations

import subprocess
from pathlib import Path
from shlex import quote

from infralink.host_readiness import HostReadinessProbe

_REMOTE_PROBE = """set -eu
printf 'hostname='; hostname
printf 'machine_id='; cat /etc/machine-id 2>/dev/null || true
for command in git docker tailscale jq bws; do
  if command -v \"$command\" >/dev/null 2>&1; then printf '%s=1\\n' \"$command\"; else printf '%s=0\\n' \"$command\"; fi
done
if command -v tailscale >/dev/null 2>&1; then
  tailscale ip -4 2>/dev/null | while IFS= read -r ip; do printf 'tailscale_ip=%s\n' "$ip"; done
  tailscale status --json 2>/dev/null | python3 -c 'import json,sys; state=json.load(sys.stdin); print("tailscale_running=" + ("1" if state.get("BackendState") == "Running" else "0")); print("tailscale_name=" + str(state.get("Self", {}).get("HostName", "")))' 2>/dev/null || printf 'tailscale_running=0\ntailscale_name=\n'
fi
if id devops >/dev/null 2>&1; then printf 'devops_account=1\\n'; else printf 'devops_account=0\\n'; fi
if test -s /home/devops/.ssh/authorized_keys; then printf 'devops_authorized_access=1\\n'; else printf 'devops_authorized_access=0\\n'; fi
if test -r /etc/infralink/host.env && grep -Eq '^[[:space:]]*BWS_ACCESS_TOKEN=.+' /etc/infralink/host.env; then printf 'bws_config=1\\n'; else printf 'bws_config=0\\n'; fi
# The controller image contains its renderer dependencies; the host needs only its launcher.
if test -x /usr/local/sbin/infralink-host; then printf 'self_deploy_dependencies=1\\n'; else printf 'self_deploy_dependencies=0\\n'; fi
if test -d /var/lib/infralink/registry && systemctl cat infralink-host-reconcile.timer >/dev/null 2>&1; then
  printf 'self_deploy_runtime=1\\nself_deploy_mode=v2_reconcile\\n'
elif test -x /opt/infra/scripts/self-deploy.sh && test -f /etc/cron.d/self-deploy; then
  printf 'self_deploy_runtime=1\\nself_deploy_mode=legacy_pull\\n'
else
  printf 'self_deploy_runtime=0\\nself_deploy_mode=\\n'
fi
if systemctl cat infralink-host-reconcile.timer >/dev/null 2>&1; then
  if systemctl is-enabled infralink-host-reconcile.timer >/dev/null 2>&1; then printf 'self_deploy_timer_enabled=1\\n'; else printf 'self_deploy_timer_enabled=0\\n'; fi
  if systemctl is-active infralink-host-reconcile.timer >/dev/null 2>&1; then printf 'self_deploy_timer_active=1\\n'; else printf 'self_deploy_timer_active=0\\n'; fi
else
  if test -f /etc/cron.d/self-deploy; then
    printf 'self_deploy_timer_enabled=1\\nself_deploy_timer_active=1\\n'
  else
    printf 'self_deploy_timer_enabled=0\\nself_deploy_timer_active=0\\n'
  fi
fi
if systemctl cat infralink-host-reconcile.service >/dev/null 2>&1; then
  reconcile_result="$(systemctl show infralink-host-reconcile.service -p Result --value 2>/dev/null || true)"
  reconcile_exit_status="$(systemctl show infralink-host-reconcile.service -p ExecMainStatus --value 2>/dev/null || true)"
  reconcile_active_state="$(systemctl show infralink-host-reconcile.service -p ActiveState --value 2>/dev/null || true)"
  reconcile_sub_state="$(systemctl show infralink-host-reconcile.service -p SubState --value 2>/dev/null || true)"
  reconcile_exit_timestamp_monotonic="$(systemctl show infralink-host-reconcile.service -p ExecMainExitTimestampMonotonic --value 2>/dev/null || true)"
  printf 'self_deploy_reconcile_result=%s\n' "$reconcile_result"
  printf 'self_deploy_reconcile_exit_status=%s\n' "$reconcile_exit_status"
  printf 'self_deploy_reconcile_active_state=%s\n' "$reconcile_active_state"
  printf 'self_deploy_reconcile_sub_state=%s\n' "$reconcile_sub_state"
  printf 'self_deploy_reconcile_exit_timestamp_monotonic=%s\n' "$reconcile_exit_timestamp_monotonic"
else
  printf 'self_deploy_reconcile_result=\nself_deploy_reconcile_exit_status=\nself_deploy_reconcile_active_state=\nself_deploy_reconcile_sub_state=\nself_deploy_reconcile_exit_timestamp_monotonic=\n'
fi
if test -L /var/lib/infralink/registry || test -L /opt/infra/registry; then
  printf 'registry_layout=unsafe\n'
elif test -d /var/lib/infralink/registry/.git && ! test -e /opt/infra/registry; then
  printf 'registry_layout=v2_managed\n'
elif test -d /opt/infra/registry/.git && ! test -e /var/lib/infralink/registry; then
  printf 'registry_layout=legacy_nested\n'
elif ! test -e /var/lib/infralink/registry && ! test -e /opt/infra/registry; then
  printf 'registry_layout=missing\n'
else
  printf 'registry_layout=unsafe\n'
fi
"""


class SshReadinessTransport:
    """Collect the bootstrap baseline over root SSH without remote mutation."""

    def __init__(
        self,
        expected_firewall_rules: tuple[str, ...] = (),
        known_hosts: Path | None = None,
    ) -> None:
        self._expected_firewall_rules = expected_firewall_rules
        self._known_hosts = known_hosts

    @property
    def known_hosts(self) -> Path | None:
        """Pinned known-hosts file used by this transport, if declared."""
        return self._known_hosts

    def probe(self, address: str) -> HostReadinessProbe:
        if not address:
            return _unreachable("host_address_missing")
        try:
            command = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "LogLevel=ERROR",
            ]
            if self._known_hosts is not None:
                command.extend(
                    [
                        "-o",
                        "StrictHostKeyChecking=yes",
                        "-o",
                        f"UserKnownHostsFile={self._known_hosts}",
                    ]
                )
            command.extend(
                [
                    f"root@{address}",
                    "sh",
                    "-s",
                ]
            )
            completed = subprocess.run(
                command,
                input=(
                    _REMOTE_PROBE
                    + _controller_python_probe()
                    + _firewall_probe(self._expected_firewall_rules)
                ),
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return _unreachable("ssh_unreachable")
        if completed.returncode != 0:
            return _unreachable("ssh_unreachable")
        values = _parse_probe(completed.stdout)
        return HostReadinessProbe(
            reachable=True,
            hostname=values.get("hostname") or None,
            machine_id=values.get("machine_id") or None,
            commands={
                name: values.get(name) == "1"
                for name in ("git", "docker", "tailscale", "jq", "bws")
            },
            devops_account=values.get("devops_account") == "1",
            devops_authorized_access=values.get("devops_authorized_access") == "1",
            bws_config=values.get("bws_config") == "1",
            self_deploy_dependencies=values.get("self_deploy_dependencies") == "1",
            self_deploy_runtime=values.get("self_deploy_runtime") == "1",
            self_deploy_timer_enabled=values.get("self_deploy_timer_enabled") == "1",
            self_deploy_timer_active=values.get("self_deploy_timer_active") == "1",
            error=None,
            self_deploy_mode=values.get("self_deploy_mode") or None,
            registry_layout=values.get("registry_layout") or "unsafe",
            self_deploy_reconcile_result=values.get("self_deploy_reconcile_result") or None,
            self_deploy_reconcile_exit_status=_optional_int(
                values.get("self_deploy_reconcile_exit_status")
            ),
            self_deploy_reconcile_active_state=(
                values.get("self_deploy_reconcile_active_state") or None
            ),
            self_deploy_reconcile_sub_state=values.get("self_deploy_reconcile_sub_state") or None,
            self_deploy_reconcile_exit_timestamp_monotonic=_optional_int(
                values.get("self_deploy_reconcile_exit_timestamp_monotonic")
            ),
            firewall_rules_expected=len(self._expected_firewall_rules),
            firewall_rules_matched=_optional_int(values.get("firewall_rules_matched")) or 0,
            firewall_observable=values.get("firewall_observable", "1") == "1",
            tailscale_ips=tuple(values.get("tailscale_ip", "").split())
            if values.get("tailscale_ip")
            else (),
            tailscale_running=values.get("tailscale_running") == "1",
            tailscale_name=values.get("tailscale_name") or None,
            controller_image=values.get("controller_image") or None,
            controller_python_version=values.get("controller_python_version") or None,
        )


def _controller_python_probe() -> str:
    return """controller_image=""
if test -r /etc/infralink/host.env; then
  set -a
  . /etc/infralink/host.env
  set +a
  controller_image="${INFRALINK_CONTROLLER_IMAGE:-}"
fi
printf 'controller_image=%s\\n' "$controller_image"
if test -n "$controller_image" && docker image inspect "$controller_image" >/dev/null 2>&1; then
  controller_python="$(docker run --pull=never --rm --entrypoint python3 "$controller_image" --version 2>&1 || true)"
  case "$controller_python" in
    "Python "[0-9]*.[0-9]*.[0-9]*) printf 'controller_python_version=%s\\n' "${controller_python#Python }" ;;
    *) printf 'controller_python_version=\\n' ;;
  esac
else
  printf 'controller_python_version=\\n'
fi
"""


def _firewall_probe(expected_rules: tuple[str, ...]) -> str:
    if not expected_rules:
        return "printf 'firewall_rules_matched=0\\nfirewall_observable=1\\n'\n"
    checks = "\n".join(
        f"if printf '%s\\n' \"$firewall_chain\" | grep -F -- {quote(rule)} >/dev/null; then firewall_rules_matched=$((firewall_rules_matched + 1)); fi"
        for rule in expected_rules
    )
    return f"""if firewall_chain=\"$(nft list chain inet infralink_filter input 2>/dev/null)\"; then
  firewall_rules_matched=0
{checks}
  printf 'firewall_observable=1\\nfirewall_rules_matched=%s\\n' \"$firewall_rules_matched\"
else
  printf 'firewall_observable=0\\nfirewall_rules_matched=0\\n'
fi
"""


def _parse_probe(stdout: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            values[key] = (
                f"{values[key]} {value}".strip()
                if key == "tailscale_ip" and key in values
                else value
            )
    return values


def _optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _unreachable(error: str) -> HostReadinessProbe:
    return HostReadinessProbe(
        False, None, None, {}, False, False, False, False, False, False, error
    )
