"""tests for feishu_hub.base_intent_router."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from feishu_hub.base_config import BaseConfig
from feishu_hub import base_intent_router as bir


@dataclass
class _FakeResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    stdout_head: str = ""
    stderr_head: str = ""
    aborted: bool = False
    abort_reason: str = ""


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
    monkeypatch.setattr(bir, "set_run_state", lambda **kw: None)

    dispatch_calls = []

    def fake_dispatch(bot_name, prompt, on_pid):
        dispatch_calls.append((bot_name, prompt))
        on_pid(12345)
        return _FakeResult(exit_code=0, stdout="hello", stdout_head="hello")
    monkeypatch.setattr(bir, "_dispatch_runner", fake_dispatch)
    monkeypatch.setattr("feishu_hub.runner_registry._pid_alive", lambda pid: True)

    registry = RunnerRegistry()
    registered = []
    orig_register = registry.register

    def spy_register(entry):
        registered.append(entry)
        orig_register(entry)
    registry.register = spy_register  # type: ignore[method-assign]

    replies = []
    event = _make_event("@bot /run 公众号-2026 record:recABC")
    assert bir.try_handle(event, configs=_configs(), registry=registry,
                          reply_fn=replies.append) is True
    assert dispatch_calls and dispatch_calls[0][0] == "selector_bot"
    assert "recABC" in dispatch_calls[0][1]
    # 期间至少 register 过一次（含 _on_pid 重注册到 12345）
    assert any(e.runner_pid == 12345 for e in registered)
    assert all(e.base_token == "K6abc" for e in registered)
    # cleanup 后 registry 应已清空
    assert registry.lookup_by_record_id("recABC") is None


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


# ---- Cycle 5.1: _dispatch_runner ----

def test_dispatch_runner_resolves_bot_and_calls_dispatcher(monkeypatch, tmp_path):
    from feishu_hub import bot_role, config
    from feishu_hub.dispatcher import runners as _runners

    fake_bot = bot_role.BotRole(
        app_id="cli_selector", role="selector_bot",
        mention_alias="选题Bot", runner="cc_headless",
        default_cwd="/tmp/work", prompt_template="x",
    )
    monkeypatch.setattr(config, "root_dir", lambda: tmp_path)
    monkeypatch.setattr(bot_role, "load_bots", lambda p: [fake_bot])

    captured = {}

    def fake_run(spec, ctx=None, *, on_pid=None):
        captured["spec"] = spec
        captured["on_pid"] = on_pid
        return "result-sentinel"

    monkeypatch.setattr(_runners, "run", fake_run)

    def _on_pid(pid: int) -> None:
        pass

    result = bir._dispatch_runner("selector_bot", "do the thing", _on_pid)
    assert result == "result-sentinel"
    spec = captured["spec"]
    assert spec.runner == "cc_headless"
    assert spec.prompt == "do the thing"
    assert spec.cwd == "/tmp/work"
    assert captured["on_pid"] is _on_pid


def test_dispatch_runner_raises_when_bot_unknown(monkeypatch, tmp_path):
    from feishu_hub import bot_role, config
    monkeypatch.setattr(config, "root_dir", lambda: tmp_path)
    monkeypatch.setattr(bot_role, "load_bots", lambda p: [])
    with pytest.raises(ValueError, match="not found"):
        bir._dispatch_runner("ghost_bot", "x", lambda pid: None)


# ---- M4.D-1: cleanup path ----

def _setup_run_env(monkeypatch, tmp_path, *, stage="📋 选题"):
    """Common boilerplate for try_handle cleanup tests."""
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    monkeypatch.setattr(bir, "base_record_get",
                        lambda **kw: {"运行状态": ["idle"], "阶段": [stage], "负责 AI": []})
    monkeypatch.setattr(bir, "cas_acquire_running",
                        lambda **kw: ("marker-x", "ok"))
    monkeypatch.setattr("feishu_hub.runner_registry._pid_alive", lambda pid: True)


def _capture_calls(monkeypatch):
    """Capture set_run_state/append_product calls; return calls dict."""
    calls = {"state": [], "product": []}
    monkeypatch.setattr(bir, "set_run_state",
                        lambda **kw: calls["state"].append(kw))
    monkeypatch.setattr(bir, "append_product",
                        lambda **kw: calls["product"].append(kw))
    return calls


def test_try_handle_writes_done_state_on_clean_exit(monkeypatch, tmp_path):
    from feishu_hub.runner_registry import RunnerRegistry
    _setup_run_env(monkeypatch, tmp_path)
    calls = _capture_calls(monkeypatch)
    monkeypatch.setattr(
        bir, "_dispatch_runner",
        lambda b, p, on_pid: _FakeResult(exit_code=0, stdout="hello", stdout_head="hello"),
    )
    registry = RunnerRegistry()
    event = _make_event("/run 公众号-2026 record:recABC")
    assert bir.try_handle(event, configs=_configs(), registry=registry,
                          reply_fn=lambda _m: None) is True
    states = [c["state"] for c in calls["state"]]
    assert "done" in states
    tails = [c["text"] for c in calls["product"]]
    assert any("hello" in t and "完成" in t for t in tails)


def test_try_handle_writes_failed_state_on_nonzero_exit(monkeypatch, tmp_path):
    from feishu_hub.runner_registry import RunnerRegistry
    _setup_run_env(monkeypatch, tmp_path)
    calls = _capture_calls(monkeypatch)
    monkeypatch.setattr(
        bir, "_dispatch_runner",
        lambda b, p, on_pid: _FakeResult(exit_code=1, stderr="err", stderr_head="err"),
    )
    registry = RunnerRegistry()
    event = _make_event("/run 公众号-2026 record:recABC")
    bir.try_handle(event, configs=_configs(), registry=registry,
                   reply_fn=lambda _m: None)
    assert any(c["state"] == "failed" for c in calls["state"])
    assert any("err" in c["text"] and "exit 1" in c["text"] for c in calls["product"])


def test_try_handle_writes_aborted_state_on_runresult_aborted(monkeypatch, tmp_path):
    from feishu_hub.runner_registry import RunnerRegistry
    _setup_run_env(monkeypatch, tmp_path)
    calls = _capture_calls(monkeypatch)
    monkeypatch.setattr(
        bir, "_dispatch_runner",
        lambda b, p, on_pid: _FakeResult(aborted=True, abort_reason="user /stop"),
    )
    registry = RunnerRegistry()
    event = _make_event("/run 公众号-2026 record:recABC")
    bir.try_handle(event, configs=_configs(), registry=registry,
                   reply_fn=lambda _m: None)
    assert any(c["state"] == "aborted" for c in calls["state"])
    assert any("user /stop" in c["text"] for c in calls["product"])


def test_try_handle_rolls_back_on_dispatch_raises(monkeypatch, tmp_path):
    from feishu_hub.runner_registry import RunnerRegistry
    _setup_run_env(monkeypatch, tmp_path)
    calls = _capture_calls(monkeypatch)

    def boom(*a, **kw):
        raise ValueError("dispatch boom")
    monkeypatch.setattr(bir, "_dispatch_runner", boom)

    registry = RunnerRegistry()
    replies = []
    event = _make_event("/run 公众号-2026 record:recABC")
    bir.try_handle(event, configs=_configs(), registry=registry,
                   reply_fn=replies.append)
    assert any(c["state"] == "failed" for c in calls["state"])
    assert registry.lookup_by_record_id("recABC") is None
    assert any("回滚" in r for r in replies)


def test_try_handle_rolls_back_on_append_product_raises(monkeypatch, tmp_path):
    from feishu_hub.runner_registry import RunnerRegistry
    from feishu_hub.lark_cli import LarkCLIError

    _setup_run_env(monkeypatch, tmp_path)
    state_calls = []
    product_calls = []

    def fake_state(**kw):
        state_calls.append(kw)

    raised = {"once": False}

    def fake_append(**kw):
        # First call (--- bot 启动 ---) raises; cleanup-time call records.
        if not raised["once"]:
            raised["once"] = True
            raise LarkCLIError(1, "base upsert failed", ["lark-cli"], stdout="", stderr="x")
        product_calls.append(kw)

    monkeypatch.setattr(bir, "set_run_state", fake_state)
    monkeypatch.setattr(bir, "append_product", fake_append)
    monkeypatch.setattr(bir, "_dispatch_runner",
                        lambda b, p, on_pid: pytest.fail("should not dispatch"))

    registry = RunnerRegistry()
    replies = []
    event = _make_event("/run 公众号-2026 record:recABC")
    bir.try_handle(event, configs=_configs(), registry=registry,
                   reply_fn=replies.append)
    assert any(c["state"] == "failed" for c in state_calls)
    assert registry.lookup_by_record_id("recABC") is None
    assert any("回滚" in r for r in replies)


def test_try_handle_unregisters_after_clean_exit(monkeypatch, tmp_path):
    from feishu_hub.runner_registry import RunnerRegistry
    _setup_run_env(monkeypatch, tmp_path)
    _capture_calls(monkeypatch)
    monkeypatch.setattr(
        bir, "_dispatch_runner",
        lambda b, p, on_pid: _FakeResult(exit_code=0, stdout="ok", stdout_head="ok"),
    )
    registry = RunnerRegistry()
    event = _make_event("/run 公众号-2026 record:recABC")
    bir.try_handle(event, configs=_configs(), registry=registry,
                   reply_fn=lambda _m: None)
    assert registry.lookup_by_record_id("recABC") is None


def test_cleanup_swallows_set_run_state_errors(monkeypatch, tmp_path):
    from feishu_hub.runner_registry import RunnerRegistry, RunnerEntry

    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))

    def boom(**kw):
        raise RuntimeError("set_run_state boom")
    monkeypatch.setattr(bir, "set_run_state", boom)
    monkeypatch.setattr(bir, "append_product", lambda **kw: None)

    registry = RunnerRegistry()
    entry = RunnerEntry(
        task_guid="base-recABC", task_url="x",
        runner_pid=0, bot_app_id="cli_local", chat_id="oc",
        source_message_id="om", started_at="t",
        record_id="recABC", base_token="K6abc", table_id="tblXYZ",
    )
    registry.register(entry)
    # Must not raise
    bir._cleanup_after_runner(
        entry=entry, bot="selector_bot",
        result=_FakeResult(exit_code=0, stdout="x", stdout_head="x"),
        registry=registry, reply_fn=lambda _m: None,
    )
    # unregister still ran despite set_run_state failure
    assert registry.lookup_by_record_id("recABC") is None
