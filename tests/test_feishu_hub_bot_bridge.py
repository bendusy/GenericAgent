"""feishu_hub.bot_bridge — 单 bot daemon：consume_im → handle_event 循环。

M3.E：relay_task 已移到 handle_event 内部；本 bridge 测试不再涉及 relay_task。
"""
from __future__ import annotations

from typing import List

import pytest

from feishu_hub import bot_bridge as bb
from feishu_hub import bot_role as br
from feishu_hub.bot_runner import BotAction


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


def test_run_bot_skips_when_handle_event_returns_none(monkeypatch):
    """unmatched 事件 handle_event 返回 None；daemon 不应 yield None。"""
    bot = _bot()

    def fake_consume(**_):
        yield {"message_id": "om_irrelevant"}

    monkeypatch.setattr(bb, "consume_im", fake_consume)
    monkeypatch.setattr(bb, "handle_event", lambda ev, b: None)

    actions = list(bb.run_bot(bot))
    assert actions == []


def test_run_bot_does_not_reference_bot_relay_task(monkeypatch):
    """M3.E：bot_bridge 不再 import bot_relay_task；relay_task 归 handle_event。

    若 bot_bridge 还在调 relay_task，下面 mock 不到属性会让 setattr 报 AttributeError，
    所以这里只验证模块属性不存在即可。
    """
    assert not hasattr(bb, "bot_relay_task"), \
        "bot_bridge should not import bot_relay_task in M3.E"


def test_run_bot_calls_cleanup_orphans_on_start(monkeypatch, tmp_path):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    from feishu_hub import runner_registry
    cleaned = []
    monkeypatch.setattr(
        runner_registry.RunnerRegistry, "cleanup_orphans",
        lambda self: cleaned.append(True) or 0,
    )
    monkeypatch.setattr(bb, "consume_im", lambda **kw: iter([]))
    list(bb.run_bot(_bot()))
    assert cleaned == [True]


def test_run_bot_routes_event_to_hitl_router_first(monkeypatch):
    """abort 命中的事件不进 handle_event。"""
    events = [
        {"message_id": "om_1", "chat_id": "oc_x"},  # 假装 hitl 命中
        {"message_id": "om_2", "chat_id": "oc_x"},  # 进 handle_event
    ]
    seen = []

    def fake_consume(**_):
        yield from events

    def fake_handle(ev, b):
        seen.append(ev["message_id"])
        return _ok_action(source_message_id=ev["message_id"])

    from feishu_hub import hitl_router
    calls = []
    def fake_dispatch(envelope, *, registry):
        calls.append(envelope["message_id"])
        if envelope["message_id"] == "om_1":
            from feishu_hub.hitl_router import AbortDecision
            return AbortDecision(chat_id="oc_x", task_guid="t",
                                 runner_pid=1, reason="/stop")
        return None

    monkeypatch.setattr(bb, "consume_im", fake_consume)
    monkeypatch.setattr(bb, "handle_event", fake_handle)
    monkeypatch.setattr(hitl_router, "dispatch", fake_dispatch)

    actions = list(bb.run_bot(_bot()))
    assert calls == ["om_1", "om_2"]
    assert seen == ["om_2"]  # om_1 被 hitl 拦
    assert [a.source_message_id for a in actions] == ["om_2"]


def test_run_bot_parallel_dispatches_handle_event_in_threads(monkeypatch):
    """parallel=True：handle_event 在 worker thread 跑；可乱序但全跑过。"""
    import threading
    events = [{"message_id": f"om_{i}", "chat_id": "oc_x"} for i in range(3)]
    main_tid = threading.get_ident()
    worker_tids = []

    def fake_consume(**_):
        yield from events

    def fake_handle(ev, b):
        worker_tids.append(threading.get_ident())
        return _ok_action(source_message_id=ev["message_id"])

    monkeypatch.setattr(bb, "consume_im", fake_consume)
    monkeypatch.setattr(bb, "handle_event", fake_handle)

    actions = list(bb.run_bot(_bot(), parallel=True))
    # 全跑过
    msgs = sorted(a.source_message_id for a in actions)
    assert msgs == ["om_0", "om_1", "om_2"]
    # handle_event 不在主线程
    assert all(tid != main_tid for tid in worker_tids)