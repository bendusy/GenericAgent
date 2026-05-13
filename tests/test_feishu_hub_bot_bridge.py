"""feishu_hub.bot_bridge — 单 bot daemon：consume_im → handle_event 循环。"""
from __future__ import annotations

from typing import List

import pytest

from feishu_hub import bot_bridge as bb
from feishu_hub import bot_role as br
from feishu_hub.bot_runner import BotAction


@pytest.fixture(autouse=True)
def _isolate_relay_task(monkeypatch):
    """所有 bot_bridge 测试默认 mock bot_relay_task.record，防止真打飞书 API。

    单独需要观察 record 行为的测试自己再覆盖 monkeypatch.setattr。
    """
    monkeypatch.setattr(bb.bot_relay_task, "record",
                        lambda **kw: None)


def _bot(**over) -> br.BotRole:
    base = dict(
        app_id="cli_aaa",
        role="reviewer",
        mention_alias="审核Bot",
        runner="cc_headless",
        default_cwd="/tmp/x",
        prompt_template="x",
    )
    base.update(over)
    return br.BotRole(**base)


def _ok_action(**over) -> BotAction:
    base = dict(
        bot_app_id="cli_aaa",
        chat_id="oc_test",
        source_message_id="om_xxx",
        reply_message_id="om_reply",
        runner_exit_code=0,
        timed_out=False,
    )
    base.update(over)
    return BotAction(**base)


# ---------------------------------------------------------------------------

def test_run_bot_dispatches_each_event_to_handle_event(monkeypatch):
    bot = _bot()
    events = [
        {"message_id": "om_1", "chat_id": "oc_test"},
        {"message_id": "om_2", "chat_id": "oc_test"},
    ]
    seen: List[dict] = []

    def fake_consume(*, profile, max_events, timeout):
        assert profile == "cli_aaa"
        yield from events

    def fake_handler(ev, b):
        seen.append(ev)
        return _ok_action(source_message_id=ev["message_id"])

    monkeypatch.setattr(bb, "consume_im", fake_consume)
    monkeypatch.setattr(bb, "handle_event", fake_handler)

    actions = list(bb.run_bot(bot, max_events=2, timeout="30s"))
    assert seen == events
    assert [a.source_message_id for a in actions] == ["om_1", "om_2"]


def test_run_bot_swallows_handler_exception_continues(monkeypatch):
    """单条事件处理崩了不能让 daemon 整体死。"""
    bot = _bot()
    events = [{"message_id": "om_1"}, {"message_id": "om_2"}]

    def fake_consume(**_):
        yield from events

    calls: List[str] = []

    def fake_handler(ev, b):
        calls.append(ev["message_id"])
        if ev["message_id"] == "om_1":
            raise RuntimeError("boom")
        return _ok_action(source_message_id="om_2")

    monkeypatch.setattr(bb, "consume_im", fake_consume)
    monkeypatch.setattr(bb, "handle_event", fake_handler)

    actions = list(bb.run_bot(bot))
    # 两条都尝试过，第二条成功
    assert calls == ["om_1", "om_2"]
    assert len(actions) == 1
    assert actions[0].source_message_id == "om_2"


def test_run_bot_invokes_relay_task_on_successful_action(monkeypatch):
    """每个成功的 BotAction 应该自动同步到飞书 Task 步骤流（M3.C T6）。"""
    bot = _bot()
    event = {
        "message_id": "om_1", "chat_id": "oc_test",
        "content": "@审核Bot  请审核 hello world",
        "chat_type": "group", "message_type": "text",
    }

    def fake_consume(**_):
        yield event

    def fake_handler(ev, b):
        return _ok_action(source_message_id=ev["message_id"])

    relay_calls: List[dict] = []

    def fake_record(*, bot, action, message_brief):
        relay_calls.append({
            "bot": bot, "action": action, "message_brief": message_brief,
        })
        return None

    monkeypatch.setattr(bb, "consume_im", fake_consume)
    monkeypatch.setattr(bb, "handle_event", fake_handler)
    monkeypatch.setattr(bb.bot_relay_task, "record", fake_record)

    list(bb.run_bot(bot))
    assert len(relay_calls) == 1
    assert relay_calls[0]["bot"].app_id == "cli_aaa"
    # message_brief 应取 @mention 剥离后的正文（前若干字符）
    assert "请审核 hello world" in relay_calls[0]["message_brief"]


def test_run_bot_continues_when_relay_task_fails(monkeypatch):
    """relay_task 调用失败不能让 daemon 死。"""
    bot = _bot()

    def fake_consume(**_):
        yield {"message_id": "om_1", "chat_id": "oc_test", "content": "@审核Bot  x"}

    def fake_handler(ev, b):
        return _ok_action()

    def boom(**kw):
        raise RuntimeError("飞书 task API 暂不可用")

    monkeypatch.setattr(bb, "consume_im", fake_consume)
    monkeypatch.setattr(bb, "handle_event", fake_handler)
    monkeypatch.setattr(bb.bot_relay_task, "record", boom)

    actions = list(bb.run_bot(bot))
    # action 仍正常 yield 出来
    assert len(actions) == 1


def test_run_bot_skips_when_handle_event_returns_none(monkeypatch):
    """unmatched 事件 handle_event 返回 None；daemon 不应 yield None。"""
    bot = _bot()

    def fake_consume(**_):
        yield {"message_id": "om_irrelevant"}

    monkeypatch.setattr(bb, "consume_im", fake_consume)
    monkeypatch.setattr(bb, "handle_event", lambda ev, b: None)

    actions = list(bb.run_bot(bot))
    assert actions == []
