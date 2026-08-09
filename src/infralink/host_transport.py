"""Read-only SSH transport for host readiness collection."""

from __future__ import annotations

import subprocess

from infralink.host_readiness import HostReadinessProbe

_REMOTE_PROBE = """set -eu
printf 'hostname='; hostname
printf 'machine_id='; cat /etc/machine-id 2>/dev/null || true
for command in git docker tailscale jq bws; do
  if command -v \"$command\" >/dev/null 2>&1; then printf '%s=1\\n' \"$command\"; else printf '%s=0\\n' \"$command\"; fi
done
if id devops >/dev/null 2>&1; then printf 'devops_account=1\\n'; else printf 'devops_account=0\\n'; fi
if test -s /home/devops/.ssh/authorized_keys; then printf 'devops_authorized_access=1\\n'; else printf 'devops_authorized_access=0\\n'; fi
if test -r /etc/environment && grep -Eq '^[[:space:]]*BWS_ACCESS_TOKEN=.+' /etc/environment; then printf 'bws_config=1\\n'; else printf 'bws_config=0\\n'; fi
if python3 -c 'import yaml, jinja2' >/dev/null 2>&1; then printf 'self_deploy_dependencies=1\\n'; else printf 'self_deploy_dependencies=0\\n'; fi
if test -d /var/lib/self-deploy-v2/runtime && systemctl cat self-deploy-v2-reconcile.timer >/dev/null 2>&1; then
  printf 'self_deploy_runtime=1\\nself_deploy_mode=v2_reconcile\\n'
elif test -x /opt/infra/scripts/self-deploy.sh && test -f /etc/cron.d/self-deploy; then
  printf 'self_deploy_runtime=1\\nself_deploy_mode=legacy_pull\\n'
else
  printf 'self_deploy_runtime=0\\nself_deploy_mode=\\n'
fi
if systemctl cat self-deploy-v2-reconcile.timer >/dev/null 2>&1; then
  if systemctl is-enabled self-deploy-v2-reconcile.timer >/dev/null 2>&1; then printf 'self_deploy_timer_enabled=1\\n'; else printf 'self_deploy_timer_enabled=0\\n'; fi
  if systemctl is-active self-deploy-v2-reconcile.timer >/dev/null 2>&1; then printf 'self_deploy_timer_active=1\\n'; else printf 'self_deploy_timer_active=0\\n'; fi
else
  if test -f /etc/cron.d/self-deploy; then
    printf 'self_deploy_timer_enabled=1\\nself_deploy_timer_active=1\\n'
  else
    printf 'self_deploy_timer_enabled=0\\nself_deploy_timer_active=0\\n'
  fi
fi
"""


class SshReadinessTransport:
    """Collect the bootstrap baseline over root SSH without remote mutation."""

    def probe(self, address: str) -> HostReadinessProbe:
        if not address:
            return _unreachable("host_address_missing")
        try:
            completed = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    "-o",
                    "LogLevel=ERROR",
                    f"root@{address}",
                    "sh",
                    "-s",
                ],
                input=_REMOTE_PROBE,
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
        )


def _parse_probe(stdout: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            values[key] = value
    return values


def _unreachable(error: str) -> HostReadinessProbe:
    return HostReadinessProbe(
        False, None, None, {}, False, False, False, False, False, False, error
    )
