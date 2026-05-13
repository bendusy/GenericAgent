"""feishu_hub.bot_relay_task — 每次接力 step → 飞书 Task。

设计：
- 每个 chat_id 在本机 cache 一个飞书 Task GUID（``~/.feishu_hub/state/m3c_chats/<chat_id>.json``）
- 首次见到该 chat 的 BotAction → 用 task_writer.create_task 建一个标题为
  ``M3.C 接力链 · <chat_id_short>`` 的飞书 task；后续 append_steps 进同一个 task
- 每条 step 含：role / bot app_id / 是否 timeout / 是否失败 / source msg_id（短化）
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from feishu_hub import bot_relay_task as brt
from feishu_hub import bot_role as br
from feishu_hub.bot_runner import BotAction
from feishu_hub.task_writer import TaskRef


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


def _action(**over) -> BotAction:
    base = dict(
        bot_app_id="cli_aaa",
        chat_id="oc_e6e50b04fc21414d6364036b23438af9",
        source_message_id="om_test_001",
        reply_message_id="om_reply_001",
        runner_exit_code=0,
        timed_out=False,
    )
    base.update(over)
    return BotAction(**base)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake_writer(monkeypatch):
    """劫持 task_writer.create_task / append_steps，记录调用。"""
    state = SimpleNamespace(creates=[], appends=[])

    def fake_create(agent, cwd, summary, *, description="", **kw):
        state.creates.append({"summary": summary, "agent": agent, "kw": kw})
        return TaskRef(guid=f"g_{len(state.creates)}", url=f"https://t.io/{len(state.creates)}")

    def fake_append(guid, steps, *, idempotency_key=None, **kw):
        state.appends.append({"guid": guid, "steps": list(steps),
                              "idempotency_key": idempotency_key, **kw})

    monkeypatch.setattr(brt.task_writer, "create_task", fake_create)
    monkeypatch.setattr(brt.task_writer, "append_steps", fake_append)
    return state


# ---------------------------------------------------------------------------

def test_record_creates_task_on_first_event_for_chat(isolated_state, fake_writer):
    bot = _bot()
    action = _action()
    ref = brt.record(bot=bot, action=action, message_brief="审核 hello")
    assert ref is not None
    assert ref.guid == "g_1"
    assert len(fake_writer.creates) == 1
    summary = fake_writer.creates[0]["summary"]
    assert "M3.C" in summary
    # chat_id 短化（末 8 字符）
    assert "23438af9" in summary
    # 第一次也要 append 一条 step
    assert len(fake_writer.appends) == 1
    assert fake_writer.appends[0]["guid"] == "g_1"


def test_record_reuses_existing_task_for_same_chat(isolated_state, fake_writer):
    bot = _bot()
    brt.record(bot=bot, action=_action(source_message_id="om_1"),
               message_brief="first")
    brt.record(bot=bot, action=_action(source_message_id="om_2"),
               message_brief="second")
    # 只该建一次 task，append 两次
    assert len(fake_writer.creates) == 1
    assert len(fake_writer.appends) == 2
    assert all(a["guid"] == "g_1" for a in fake_writer.appends)


def test_record_separates_tasks_per_chat(isolated_state, fake_writer):
    bot = _bot()
    brt.record(bot=bot, action=_action(chat_id="oc_aaa"), message_brief="m1")
    brt.record(bot=bot, action=_action(chat_id="oc_bbb"), message_brief="m2")
    assert len(fake_writer.creates) == 2
    assert {a["guid"] for a in fake_writer.appends} == {"g_1", "g_2"}


def test_step_content_includes_role_and_brief_and_status(isolated_state, fake_writer):
    bot = _bot(role="reviewer")
    brt.record(bot=bot, action=_action(), message_brief="审核 hello")
    step = fake_writer.appends[0]["steps"][0]
    assert "reviewer" in step
    assert "审核 hello" in step
    # 成功路径不带 ❌ / ⚠️
    assert "❌" not in step and "⚠️" not in step


def test_step_content_marks_timeout(isolated_state, fake_writer):
    bot = _bot()
    brt.record(bot=bot,
               action=_action(timed_out=True, runner_exit_code=-1),
               message_brief="x")
    step = fake_writer.appends[0]["steps"][0]
    assert "⚠️" in step or "timeout" in step.lower()


def test_step_content_marks_runner_failure(isolated_state, fake_writer):
    bot = _bot()
    brt.record(bot=bot,
               action=_action(runner_exit_code=1),
               message_brief="x")
    step = fake_writer.appends[0]["steps"][0]
    assert "❌" in step or "exit=1" in step


def test_record_routes_to_relay_writer_profile_when_set(isolated_state, monkeypatch):
    """relay_writer_app_id 非空 → task_writer.create_task / append_steps 都带 profile=
    那个 app_id，让两机的 relay_task 在同一身份下收敛到同一 task guid。"""
    create_calls = []
    append_calls = []

    def fake_create(agent, cwd, summary, *, description="", **kw):
        create_calls.append(kw)
        return TaskRef(guid="g1", url="u1")

    def fake_append(guid, steps, *, idempotency_key=None, profile=None):
        append_calls.append({"profile": profile})

    monkeypatch.setattr(brt.task_writer, "create_task", fake_create)
    monkeypatch.setattr(brt.task_writer, "append_steps", fake_append)

    bot = _bot(relay_writer_app_id="cli_central_writer")
    brt.record(bot=bot, action=_action(), message_brief="x")
    assert create_calls[0]["profile"] == "cli_central_writer"
    assert append_calls[0]["profile"] == "cli_central_writer"


def test_record_default_profile_none_when_no_writer_configured(isolated_state, monkeypatch):
    """relay_writer_app_id 空 = 不传 --profile，沿用当前 active profile（向后兼容）。"""
    create_calls = []
    append_calls = []

    def fake_create(agent, cwd, summary, *, description="", **kw):
        create_calls.append(kw)
        return TaskRef(guid="g1", url="u1")

    def fake_append(guid, steps, *, idempotency_key=None, profile=None):
        append_calls.append({"profile": profile})

    monkeypatch.setattr(brt.task_writer, "create_task", fake_create)
    monkeypatch.setattr(brt.task_writer, "append_steps", fake_append)

    bot = _bot()
    brt.record(bot=bot, action=_action(), message_brief="x")
    assert create_calls[0].get("profile") in (None, "")
    assert append_calls[0]["profile"] in (None, "")


def test_record_uses_idempotent_step_key(isolated_state, fake_writer):
    """同一 source_message_id × bot 重复调 → idempotency_key 相同。"""
    bot = _bot()
    brt.record(bot=bot, action=_action(source_message_id="om_777"),
               message_brief="x")
    brt.record(bot=bot, action=_action(source_message_id="om_777"),
               message_brief="x")
    k1 = fake_writer.appends[0]["idempotency_key"]
    k2 = fake_writer.appends[1]["idempotency_key"]
    assert k1 and k1 == k2
