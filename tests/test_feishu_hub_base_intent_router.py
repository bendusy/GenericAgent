"""tests for feishu_hub.base_intent_router."""
from __future__ import annotations

import json

import pytest

from feishu_hub.base_config import BaseConfig
from feishu_hub import base_intent_router as bir


def _configs():
    return [BaseConfig(role="公众号-2026", base_token="K6abc", table_id="tblXYZ",
                       stage_to_bot={"📋 选题": "selector_bot"})]


# ---- Cycle 4.1: _parse_base_ref ----

def test_parse_url_form():
    text = "请处理 https://feishu.cn/base/K6abc?table=tblXYZ&record=recABC 谢谢"
    assert bir._parse_base_ref(text, _configs()) == ("K6abc", "tblXYZ", "recABC")


def test_parse_short_ref():
    text = "公众号-2026 record:recABC"
    assert bir._parse_base_ref(text, _configs()) == ("K6abc", "tblXYZ", "recABC")


def test_parse_short_ref_unknown_role_returns_none():
    text = "未知角色 record:recABC"
    assert bir._parse_base_ref(text, _configs()) is None


def test_parse_garbage_returns_none():
    assert bir._parse_base_ref("hello world", _configs()) is None


def test_parse_url_with_extra_params_still_works():
    text = "https://feishu.cn/base/K6abc?table=tblXYZ&foo=bar&record=recABC"
    assert bir._parse_base_ref(text, _configs()) == ("K6abc", "tblXYZ", "recABC")


# ---- Cycle 4.2: _resolve_bot ----

def test_resolve_bot_prefers_负责AI():
    cfg = _configs()[0]
    rec = {"负责 AI": ["drafter_bot"], "阶段": ["📋 选题"]}
    assert bir._resolve_bot(rec, cfg) == "drafter_bot"


def test_resolve_bot_falls_back_to_stage_map():
    cfg = _configs()[0]
    rec = {"负责 AI": [], "阶段": ["📋 选题"]}
    assert bir._resolve_bot(rec, cfg) == "selector_bot"


def test_resolve_bot_returns_none_when_stage_unmapped():
    cfg = _configs()[0]
    rec = {"负责 AI": [], "阶段": ["未注册阶段"]}
    assert bir._resolve_bot(rec, cfg) is None


def test_resolve_bot_returns_none_when_record_has_neither():
    cfg = _configs()[0]
    rec = {"负责 AI": [], "阶段": []}
    assert bir._resolve_bot(rec, cfg) is None


# ---- Cycle 4.3: try_handle happy path + no-op ----

def _make_event(text: str, chat_id: str = "oc_x", msg_id: str = "om_x") -> dict:
    return {"event": {"message": {
        "content": json.dumps({"text": text}),
        "chat_id": chat_id, "message_id": msg_id,
    }}}


def test_try_handle_returns_false_when_no_run_command(monkeypatch, tmp_path):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    from feishu_hub.runner_registry import RunnerRegistry
    registry = RunnerRegistry()
    replies = []
    event = _make_event("hi there")
    assert bir.try_handle(event, configs=_configs(), registry=registry,
                          reply_fn=replies.append) is False
    assert replies == []


def test_try_handle_consumes_run_command_happy_path(monkeypatch, tmp_path):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    from feishu_hub.runner_registry import RunnerRegistry

    monkeypatch.setattr(bir, "base_record_get",
                        lambda **kw: {"运行状态": ["idle"], "阶段": ["📋 选题"], "负责 AI": []})
    monkeypatch.setattr(bir, "cas_acquire_running",
                        lambda **kw: ("marker-abc", "ok"))
    monkeypatch.setattr(bir, "append_product", lambda **kw: None)

    dispatch_calls = []

    def fake_dispatch(bot_name, prompt, on_pid):
        dispatch_calls.append((bot_name, prompt))
        on_pid(12345)
        return 12345
    monkeypatch.setattr(bir, "_dispatch_runner", fake_dispatch)
    monkeypatch.setattr("feishu_hub.runner_registry._pid_alive", lambda pid: True)

    registry = RunnerRegistry()
    replies = []
    event = _make_event("@bot /run 公众号-2026 record:recABC")
    assert bir.try_handle(event, configs=_configs(), registry=registry,
                          reply_fn=replies.append) is True
    assert dispatch_calls and dispatch_calls[0][0] == "selector_bot"
    assert "recABC" in dispatch_calls[0][1]
    entry = registry.lookup_by_record_id("recABC")
    assert entry is not None
    assert entry.runner_pid == 12345
    assert entry.base_token == "K6abc"


# ---- Cycle 4.4: try_handle reject paths ----

class _FakeRegistry:
    """Minimal RunnerRegistry stub for reject-path tests."""

    def __init__(self, existing=None):
        self._existing = existing
        self.registered = []

    def lookup_by_record_id(self, record_id):
        return self._existing

    def register(self, entry):
        self.registered.append(entry)


def test_try_handle_replies_unknown_ref(monkeypatch, tmp_path):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    replies = []
    event = _make_event("/run garbage")
    assert bir.try_handle(event, configs=_configs(), registry=_FakeRegistry(),
                          reply_fn=replies.append) is True
    assert any("无法解析" in r for r in replies)


def test_try_handle_replies_unknown_base_token_after_url_parse(monkeypatch, tmp_path):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    replies = []
    event = _make_event(
        "/run https://feishu.cn/base/Kunknown?table=tblXYZ&record=recABC"
    )
    assert bir.try_handle(event, configs=_configs(), registry=_FakeRegistry(),
                          reply_fn=replies.append) is True
    assert any("未注册" in r for r in replies)


def test_try_handle_replies_when_record_already_running(monkeypatch, tmp_path):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    from feishu_hub.runner_registry import RunnerEntry

    existing = RunnerEntry(
        task_guid="base-recABC", task_url="x", runner_pid=1, bot_app_id="b",
        chat_id="c", source_message_id="m", started_at="t",
        record_id="recABC", base_token="K6abc", table_id="tblXYZ",
    )
    replies = []
    event = _make_event("/run 公众号-2026 record:recABC")
    assert bir.try_handle(event, configs=_configs(),
                          registry=_FakeRegistry(existing=existing),
                          reply_fn=replies.append) is True
    assert any("已有 runner" in r for r in replies)


def test_try_handle_replies_when_record_non_idle(monkeypatch, tmp_path):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    monkeypatch.setattr(bir, "base_record_get",
                        lambda **kw: {"运行状态": ["running"], "阶段": ["📋 选题"], "负责 AI": []})
    monkeypatch.setattr(bir, "cas_acquire_running",
                        lambda **kw: (None, "non_idle"))
    replies = []
    event = _make_event("/run 公众号-2026 record:recABC")
    assert bir.try_handle(event, configs=_configs(), registry=_FakeRegistry(),
                          reply_fn=replies.append) is True
    assert any("非 idle" in r for r in replies)


def test_try_handle_replies_when_concurrent_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    monkeypatch.setattr(bir, "base_record_get",
                        lambda **kw: {"运行状态": ["idle"], "阶段": ["📋 选题"], "负责 AI": []})
    monkeypatch.setattr(bir, "cas_acquire_running",
                        lambda **kw: (None, "concurrent_conflict"))
    replies = []
    event = _make_event("/run 公众号-2026 record:recABC")
    assert bir.try_handle(event, configs=_configs(), registry=_FakeRegistry(),
                          reply_fn=replies.append) is True
    assert any("并发" in r for r in replies)


def test_try_handle_replies_when_no_bot_resolved(monkeypatch, tmp_path):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    monkeypatch.setattr(
        bir, "base_record_get",
        lambda **kw: {"运行状态": ["idle"], "阶段": ["未注册阶段"], "负责 AI": []},
    )
    replies = []
    event = _make_event("/run 公众号-2026 record:recABC")
    assert bir.try_handle(event, configs=_configs(), registry=_FakeRegistry(),
                          reply_fn=replies.append) is True
    assert any("未绑 bot" in r for r in replies)
