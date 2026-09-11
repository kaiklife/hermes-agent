"""Per-task model override for delegate_task(tasks=[{goal, model}, ...]).

Contract: a task's own ``model`` reaches its child; a task without one falls
back to the call-level route (delegation.model → parent model). Never reads
source text — drives the real dispatch and asserts on the kwargs handed to
child construction.
"""

import types

import tools.delegate_tool as dt

GOOD_A = "Refactor the login handler to use the new session helper"
GOOD_B = "Write regression tests for the session expiry watcher"


def _parent():
    # Minimal parent the batch path touches; see test_delegate_batch_tag.
    return types.SimpleNamespace(
        session_id="root", model="parent-model", tool_progress_callback=None,
        _delegate_spinner=None, _safe_print=lambda line: None, _delegate_depth=0,
    )


def _spawn(monkeypatch, tmp_path, creds_model, tasks):
    """Drive delegate_task; return the model kwarg passed per task, in order."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir(exist_ok=True)
    models = []
    monkeypatch.setattr(
        dt, "_build_child_preserving_parent_tools",
        lambda **kw: (models.append(kw["model"]), types.SimpleNamespace(tool_progress_callback=None))[1],
    )
    monkeypatch.setattr(dt, "_resolve_delegation_credentials", lambda *a, **k: {
        "model": creds_model, "provider": "openrouter", "base_url": "https://x/v1",
        "api_key": "k", "api_mode": "chat_completions"})
    monkeypatch.setattr(dt, "_run_single_child", lambda task_index, goal, child=None, parent_agent=None, **kw: {
        "task_index": task_index, "status": "completed", "summary": "ok",
        "error": None, "api_calls": 1, "duration_seconds": 1})
    dt.delegate_task(tasks=tasks, parent_agent=_parent())
    return models


def test_per_task_model_overrides_apply_independently(monkeypatch, tmp_path):
    models = _spawn(monkeypatch, tmp_path, "deleg-model", [
        {"goal": GOOD_A, "model": "glm-5.2"},
        {"goal": GOOD_B, "model": "deepseek-v4-flash"},
    ])
    assert models == ["glm-5.2", "deepseek-v4-flash"]


def test_whitespace_padded_task_model_is_trimmed(monkeypatch, tmp_path):
    models = _spawn(monkeypatch, tmp_path, "deleg-model", [
        {"goal": GOOD_A, "model": "  glm-5.2  "},
    ])
    assert models == ["glm-5.2"]


def test_non_string_or_blank_task_model_falls_back_to_call_level(monkeypatch, tmp_path):
    """Only a non-empty string overrides; absent, null, empty/whitespace and non-string
    (which must not crash nor be coerced into a bogus model id) all fall back."""
    models = _spawn(monkeypatch, tmp_path, "deleg-model", [
        {"goal": GOOD_A},                     # absent
        {"goal": GOOD_B, "model": None},      # explicit null
        {"goal": GOOD_A, "model": ""},        # empty string
        {"goal": GOOD_B, "model": "   "},     # whitespace only
        {"goal": GOOD_A, "model": 42},        # non-string must not become "42"
    ])
    assert models == ["deleg-model"] * 5
