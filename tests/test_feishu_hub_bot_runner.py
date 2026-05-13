"""feishu_hub.bot_runner — IM event → runner → thread reply。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from feishu_hub import bot_role as br
from feishu_hub import bot_runner as r
from feishu_hub.dispatcher.runners import RunResult, RunSpec


def _bot(**over) -> br.BotRole:
    base = dict(
        app_id="cli_aaa",
        role="reviewer",
        mention_alias="审核Bot",
        runner="cc_headless",
        default_cwd="/tmp/x",
        prompt_template="请审核：{message}",
        reply_template="{result}",
    )
    base.update(over)
    return br.BotRole(**base)


def _ev(**over) -> dict:
    base = {
        "chat_type": "group",
        "message_type": "text",
        "chat_id": "oc_test",
        "content": "@审核Bot  请检查 hello world",
        "sender_id": "ou_user",
        "message_id": "om_xxx",
    }
    base.update(over)
    return base


def _ok_result(text: str = "✅ 通过") -> RunResult:
    return RunResult(
        runner="cc_headless",
        exit_code=0,
        stdout=text,
        stderr="",
        stdout_head=text,
        stderr_head="",
        duration_ms=12,
        timed_out=False,
        final_text=text,
    )


# ---------------------------------------------------------------------------

def test_handle_event_returns_none_when_event_does_not_match():
    bot = _bot()
    ev = _ev(content="hi @沉淀Bot something")  # @不是这个 bot
    result = r.handle_event(ev, bot, runner=lambda s: _ok_result(), replier=None)
    assert result is None


def test_handle_event_formats_prompt_with_stripped_body():
    bot = _bot()
    ev = _ev(content="@审核Bot  please review this")
    captured: dict = {}

    def fake_runner(spec: RunSpec) -> RunResult:
        captured["prompt"] = spec.prompt
        captured["cwd"] = spec.cwd
        captured["runner"] = spec.runner
        return _ok_result()

    def fake_replier(**kw):
        return "om_reply_1"

    r.handle_event(ev, bot, runner=fake_runner, replier=fake_replier)
    assert captured["prompt"] == "请审核：please review this"
    assert captured["cwd"] == "/tmp/x"
    assert captured["runner"] == "cc_headless"


def test_handle_event_replies_in_thread_with_runner_result():
    bot = _bot(reply_template="审核结果：{result}")
    ev = _ev()
    captured: dict = {}

    def fake_replier(**kw):
        captured.update(kw)
        return "om_reply_1"

    action = r.handle_event(
        ev, bot,
        runner=lambda s: _ok_result("✅ 通过"),
        replier=fake_replier,
    )
    assert captured["message_id"] == "om_xxx"
    assert captured["text"] == "审核结果：✅ 通过"
    assert captured["thread"] is True
    assert captured["profile"] == "cli_aaa"
    assert action.reply_message_id == "om_reply_1"
    assert action.runner_exit_code == 0


def test_handle_event_appends_next_bot_mention_to_reply():
    bot = _bot(
        reply_template="审核结果：{result}",
        next_bot_mention="@沉淀Bot",
    )
    ev = _ev()
    captured: dict = {}

    def fake_replier(**kw):
        captured.update(kw)
        return "om_reply_1"

    r.handle_event(
        ev, bot,
        runner=lambda s: _ok_result("✅ 通过"),
        replier=fake_replier,
    )
    # next_bot_mention 应被追加到 reply text 末尾（独立一行）
    assert "@沉淀Bot" in captured["text"]
    assert captured["text"].endswith("@沉淀Bot 请接力。")


def test_handle_event_reports_runner_failure_in_reply():
    bot = _bot()
    ev = _ev()
    bad = RunResult(
        runner="cc_headless", exit_code=1,
        stdout="", stderr="boom",
        stdout_head="", stderr_head="boom",
        duration_ms=5, timed_out=False, final_text=None,
    )
    captured: dict = {}

    def fake_replier(**kw):
        captured.update(kw)
        return "om_reply_err"

    action = r.handle_event(
        ev, bot, runner=lambda s: bad, replier=fake_replier,
    )
    assert action.runner_exit_code == 1
    assert "runner failed" in captured["text"] or "boom" in captured["text"]


def test_handle_event_skips_when_runner_timed_out():
    """超时的 runner result：仍然回 IM（plan C3=C），但带 timeout 标识。"""
    bot = _bot()
    ev = _ev()
    timed = RunResult(
        runner="cc_headless", exit_code=-1,
        stdout="", stderr="",
        stdout_head="", stderr_head="",
        duration_ms=600_000, timed_out=True, final_text=None,
    )
    captured: dict = {}

    def fake_replier(**kw):
        captured.update(kw)
        return "om_reply_timeout"

    action = r.handle_event(
        ev, bot, runner=lambda s: timed, replier=fake_replier,
    )
    assert action.timed_out is True
    assert "timeout" in captured["text"].lower() or "超时" in captured["text"]
