#!/usr/bin/env bash
# Restart the supervised gateway from OUTSIDE its own process tree.
#
# A plain `systemctl restart hermes-gateway.service` run by the terminal tool
# dies with the thing it is restarting: the gateway is the parent of that
# subprocess, so SIGTERM reaches the command before systemd finishes the job
# (tools/terminal_tool_guards.gateway_lifecycle_block refuses it until the
# restart/stop/uninstall approval key is granted).
#
# Handing the job to systemd instead fixes the shape of the restart: the
# transient unit is owned by PID 1, not by the gateway, so the gateway's
# SIGTERM has nothing to propagate to and the pending restart still fires.
#
#   --on-active=3  wake 3s from now, leaving the current reply time to flush
#                  to the user before the gateway goes down underneath it.
#   --collect      discard the transient unit after it runs, so repeated
#                  restarts do not pile up inactive units.
#
# Intentionally NOT bypassing the guard: this file is scanned when something
# references it, so reaching it already requires the approval.
set -euo pipefail

sudo systemd-run --collect --unit="hermes-gw-restart-$(date +%s)" --on-active=3 \
  /bin/systemctl restart hermes-gateway.service
