"""The supervised-gateway lifecycle guard is approvable — and the approval persists.

Contracts under test, driven through the real functions against a temp
``HERMES_HOME`` (AGENTS.md: E2E with real imports, no mocking of the seam under
test):

* an unapproved in-gateway restart is refused with a message that names the
  exact key to approve;
* a grant loaded from ``command_allowlist`` releases the guard, so the approval
  survives a process restart;
* the ``launchctl submit``/``bootstrap`` diagnostic branch ignores the grant —
  approving the restart guard must not widen it, including when the optional
  pre-scan cannot run (exhausted budget is not a verdict);
* a grant never drops existing ``command_allowlist`` entries, even when the
  config cannot be re-read at save time;
* ``approve_gateway_lifecycle()`` is idempotent and leaves the rest of the
  config alone.
"""

import json
from pathlib import Path

import pytest
import yaml

import hermes_cli.config as hc
from tools import approval as approval_module
from tools import process_registry
from tools.approval import (
    approve_gateway_lifecycle,
    is_approved,
    load_permanent_allowlist,
)
from tools.terminal_tool_guards import GATEWAY_LIFECYCLE_PATTERN_KEY, gateway_lifecycle_block

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_KEY = "gw-lifecycle-test"


@pytest.fixture
def clean_approvals(monkeypatch):
    """Isolate the process-global approval sets: both are read by name at call
    time, so rebinding the module attribute is enough (and monkeypatch restores
    the real objects afterwards)."""
    monkeypatch.setattr(approval_module, "_permanent_approved", set())
    monkeypatch.setattr(approval_module, "_session_approved", {})


@pytest.fixture
def supervised(monkeypatch):
    """Pretend this process is the systemd-supervised gateway — the only state
    the guard's outer gate reads."""
    monkeypatch.setattr(process_registry, "_is_supervised_gateway_process", lambda: True)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    _write_config(home, {"command_allowlist": ["git push *"]})
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home
    hc._LOAD_CONFIG_CACHE.clear()


def _write_config(home: Path, extra: dict) -> Path:
    hc._LOAD_CONFIG_CACHE.clear()
    config = {
        "model": {"default": "test-model"},
        "approvals": {"mode": "manual", "timeout": 300},
        "security": {"tirith_enabled": False},
    }
    config.update(extra)
    path = home / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _verdict(command: str, cwd: Path) -> str | None:
    return gateway_lifecycle_block(
        command=command, env=None, env_type="local", cwd=str(cwd),
        workdir=None, session_key=SESSION_KEY,
    )


def test_restart_is_refused_until_approved(supervised, clean_approvals, tmp_path):
    blocked = _verdict("systemctl restart hermes-gateway.service", tmp_path)
    assert blocked is not None
    result = json.loads(blocked)
    assert result["exit_code"] == 1
    assert "restart, stop, or uninstall the gateway" in result["error"]
    # The rejection must be actionable: it names the very string that grants it.
    assert GATEWAY_LIFECYCLE_PATTERN_KEY in result["error"]
    assert "/approve" in result["error"]
    assert "command_allowlist" in result["error"]


def test_benign_gateway_query_is_never_blocked(supervised, clean_approvals, tmp_path):
    assert _verdict("systemctl status hermes-gateway.service", tmp_path) is None


def test_grant_loaded_from_config_releases_the_guard(supervised, clean_approvals, config_home, tmp_path):
    """A restart after a process start reads the key off disk — the approval is
    durable, not just in-memory."""
    _write_config(config_home, {"command_allowlist": [GATEWAY_LIFECYCLE_PATTERN_KEY]})
    assert GATEWAY_LIFECYCLE_PATTERN_KEY in load_permanent_allowlist()
    assert _verdict("systemctl restart hermes-gateway.service", tmp_path) is None


def test_launchctl_submit_branch_ignores_the_grant(supervised, clean_approvals, tmp_path):
    """Approving the restart guard must not open persistent-job registration."""
    approval_module.approve_permanent(GATEWAY_LIFECYCLE_PATTERN_KEY)
    assert is_approved(SESSION_KEY, GATEWAY_LIFECYCLE_PATTERN_KEY)
    blocked = _verdict("launchctl submit -l com.example.keepalive /bin/true", tmp_path)
    assert blocked is not None
    assert "regardless of the job label" in json.loads(blocked)["error"]


def test_referenced_restart_script_shares_the_verdict(supervised, clean_approvals, tmp_path):
    """The detached-restart helper is caught through the script scan, so it only
    runs once the guard has been granted — by design."""
    command = f"bash {REPO_ROOT / 'scripts' / 'gateway_restart_detached.sh'}"
    assert _verdict(command, tmp_path) is not None
    approval_module.approve_permanent(GATEWAY_LIFECYCLE_PATTERN_KEY)
    assert _verdict(command, tmp_path) is None


def test_approve_gateway_lifecycle_persists_once_and_keeps_the_rest_of_config(
        clean_approvals, config_home):
    approve_gateway_lifecycle()
    approve_gateway_lifecycle()

    raw = yaml.safe_load((config_home / "config.yaml").read_text())
    entries = raw["command_allowlist"]
    assert entries.count(GATEWAY_LIFECYCLE_PATTERN_KEY) == 1
    assert "git push *" in entries

    # Nothing else in the file was clobbered by the write.
    hc._LOAD_CONFIG_CACHE.clear()
    config = hc.load_config()
    assert config["model"]["default"] == "test-model"
    assert config["approvals"]["mode"] == "manual"
    assert config["security"]["tirith_enabled"] is False


def test_approval_survives_a_simulated_restart(clean_approvals, config_home, tmp_path, supervised):
    """Wipe the in-process sets (what a restart does) and re-seed from disk: the
    guard must still be released, and by the real entry point."""
    approve_gateway_lifecycle()
    approval_module._permanent_approved.clear()
    assert not is_approved(SESSION_KEY, GATEWAY_LIFECYCLE_PATTERN_KEY)

    hc._LOAD_CONFIG_CACHE.clear()
    load_permanent_allowlist()
    assert is_approved(SESSION_KEY, GATEWAY_LIFECYCLE_PATTERN_KEY)
    assert _verdict("systemctl restart hermes-gateway.service", tmp_path) is None


def test_over_budget_launchctl_submit_is_blocked_even_when_approved(
        supervised, clean_approvals, tmp_path, monkeypatch):
    """The launchctl pre-scan is optional; when it cannot run the command must
    reach the full fail-closed guard, not the approval shortcut. A grant therefore
    never opens persistent-job registration — walk the real guard with the scan
    budget lowered below the command length."""
    from cron import lifecycle_guard
    monkeypatch.setattr(lifecycle_guard, "_MAX_LIFECYCLE_SCAN_BYTES", 8)
    assert not lifecycle_guard.lifecycle_scan_root_within_budget(
        "launchctl submit -l com.example.keepalive /bin/true")

    approval_module.approve_permanent(GATEWAY_LIFECYCLE_PATTERN_KEY)
    assert is_approved(SESSION_KEY, GATEWAY_LIFECYCLE_PATTERN_KEY)
    assert _verdict("launchctl submit -l com.example.keepalive /bin/true", tmp_path) is not None


def test_grant_keeps_existing_entries_when_the_config_cannot_be_re_read(
        clean_approvals, config_home, monkeypatch):
    """``load_permanent_allowlist`` returns an empty set on a failed read; merging
    from that alone would save ``[KEY]`` over ``git push *``. The in-process set is
    the fallback, so an existing entry survives an unreadable config."""
    approval_module.load_permanent_allowlist()  # seed in-process from disk

    def unreadable():
        raise OSError("config unreadable")

    monkeypatch.setattr(hc, "load_config_readonly", unreadable)
    approve_gateway_lifecycle()

    entries = yaml.safe_load((config_home / "config.yaml").read_text())["command_allowlist"]
    assert "git push *" in entries
    assert GATEWAY_LIFECYCLE_PATTERN_KEY in entries
