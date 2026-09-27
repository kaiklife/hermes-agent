"""Guards on the promoted-reasoning path: a degenerate short-line loop must never be
promoted to the user-visible reply, and Chinese planning tails must be caught.

Live incident (2026-09-27): deepseek-v4.1-flash ended four consecutive turns on a
reasoning-only clean stop whose reasoning was a short-line loop ("嗯，好。\\n\\n跑。\\n\\n好。").
``agent.turn_final_response`` promoted it to the final answer, the gateway delivered it
verbatim (delivered length matched the reasoning length exactly), and the promoted text was
stamped into the ``api_content`` sidecar — which overrides ``content`` at API-build time, so
the next turn replayed the loop and the model regenerated it.

Both pre-existing repetition detectors missed the shape: ``is_repetition_dominated`` needs a
60+ char window to dominate and ``_line_repetition_dominated`` needs one line to cover half
the text; a short-line loop produces neither. The distinct-line ratio still separates it —
measured on the stored reasoning of the four real turns: loop 0.25-0.36, real replies
0.75-1.00 — which is why ``is_short_line_loop`` gates on that ratio directly.
"""

from agent.agent_runtime_helpers import promoted_reasoning_announces_action
from agent.repetition_guard import (
    is_repetition_dominated, is_runaway_repetition, is_short_line_loop,
)

# Rebuilt to the measured shape of the incident text: a few very short lines cycling, with a
# distinct "note" line every few cycles. The irregular notes are what keep a 60-char sliding
# window from aligning, so the older detectors stay False exactly as they did on the real text.
_NOTES = (
    "他在描述", "关键", "我该", "而且", "够", "写", "开始", "关键", "而且", "开始",
    "查", "写", "够", "而且", "开始", "写", "关键", "写", "开始", "而且",
)
_SHORT_LINE_LOOP = "".join(
    "嗯，好。\n\n跑。\n\n好。\n\n嗯，**写** ✅\n\n好。\n\n跑。\n\n"
    + f"嗯，**{note}** ✅\n\n好。\n\n跑。\n\n"
    for note in _NOTES
)


def test_short_line_loop_is_caught_where_the_older_detectors_miss_it():
    assert is_short_line_loop(_SHORT_LINE_LOOP)
    assert not is_repetition_dominated(_SHORT_LINE_LOOP)
    assert not is_runaway_repetition(_SHORT_LINE_LOOP)


def test_short_line_loop_ignores_real_replies_and_non_strings():
    distinct_list = "".join(f"- 第 {i} 项检查通过，耗时 {i * 3} 毫秒。\n" for i in range(40))
    for text in (
        distinct_list,                                          # long, but every line distinct
        "第一段。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。",   # too few lines to judge
        "",
    ):
        assert not is_short_line_loop(text), text
    # Defensive: non-string input must not raise — the function is called with whatever the
    # provider put in the reasoning field.
    for junk in (None, 12345):
        assert not is_short_line_loop(junk)  # type: ignore[arg-type]


def test_promoted_reasoning_detector_catches_chinese_plan_tails():
    # The existing detector covered English + Thai only, so a Chinese planning tail promoted
    # verbatim (#111761's sibling case).
    for tail in (
        "查完了。让我先看一下日志。",
        "分析完毕。我需要确认一下配置。",
        "嗯，好。我来查一下。",
        "接下来我要跑测试。",
        "读了半天，现在我去看看进程。",
    ):
        assert promoted_reasoning_announces_action(tail), tail


def test_promoted_reasoning_detector_ignores_chinese_stated_answers():
    for text in (
        "答案就是 42。",
        "我看了一下，没问题。",
        "我需要的是休息，不是别的。",
        "让我想起了小时候。",
    ):
        assert not promoted_reasoning_announces_action(text), text
