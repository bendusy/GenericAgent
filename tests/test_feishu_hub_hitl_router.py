import dataclasses
import os
import signal

import pytest

from feishu_hub.hitl_router import dispatch, AbortDecision, ABORT_KEYWORDS
from feishu_hub.runner_registry import RunnerEntry, RunnerRegistry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    return RunnerRegistry()


def _envelope(content="/stop", chat_id="oc_x", sender="ou_user"):
    return {
        "content": content,
        "chat_id": chat_id,
        "message_id": "om_x",
        "sender_id": sender,
    }


def _entry(*, chat_id, pid):
    return RunnerEntry(
        task_guid=f"t-{chat_id}", task_url="u", runner_pid=pid,
        bot_app_id="cli_x", chat_id=chat_id, source_message_id="om_x",
        started_at="2026-05-13T22:30:00+08:00",
    )


def test_dispatch_keyword_hit_kills_runner(registry, monkeypatch):
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    registry.register(_entry(chat_id="oc_x", pid=11111))
    decision = dispatch(_envelope(content="/stop kill"), registry=registry)
    assert isinstance(decision, AbortDecision)
    assert decision.chat_id == "oc_x"
    assert decision.runner_pid == 11111
    assert decision.reason == "/stop"
    assert killed == [(11111, signal.SIGTERM)]
    # sentinel 已写
    assert registry.read_abort_sentinel("t-oc_x") == "/stop"


def test_dispatch_returns_none_when_no_keyword(registry, monkeypatch):
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    registry.register(_entry(chat_id="oc_x", pid=11111))
    assert dispatch(_envelope(content="just chatting"), registry=registry) is None
    assert registry.read_abort_sentinel("t-oc_x") is None


def test_dispatch_returns_none_when_chat_has_no_runner(registry, monkeypatch):
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    # 没 register
    assert dispatch(_envelope(content="/stop"), registry=registry) is None


def test_dispatch_returns_none_on_empty_chat_id(registry, monkeypatch):
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    registry.register(_entry(chat_id="oc_x", pid=11111))
    assert dispatch(_envelope(content="/stop", chat_id=""),
                    registry=registry) is None


def test_dispatch_handles_dead_pid_gracefully(registry, monkeypatch):
    def fake_kill(pid, sig):
        raise ProcessLookupError(f"pid {pid} gone")
    monkeypatch.setattr(os, "kill", fake_kill)
    registry.register(_entry(chat_id="oc_x", pid=999999))
    # 即使 kill 抛 ProcessLookupError，sentinel 仍写、decision 仍返回（POC 容错）
    decision = dispatch(_envelope(content="/stop"), registry=registry)
    assert decision is not None
    assert registry.read_abort_sentinel("t-oc_x") == "/stop"


@pytest.mark.parametrize("kw", ABORT_KEYWORDS)
def test_dispatch_recognizes_all_keywords(registry, monkeypatch, kw):
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    registry.register(_entry(chat_id="oc_x", pid=11111))
    decision = dispatch(_envelope(content=f"{kw} 求你了"), registry=registry)
    assert decision is not None and decision.reason == kw


def test_dispatch_ignores_keyword_in_middle_of_message(registry, monkeypatch):
    """user 说"快/stop 啊"不算干预——必须以关键词起头。"""
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    registry.register(_entry(chat_id="oc_x", pid=11111))
    assert dispatch(_envelope(content="快 /stop 啊"),
                    registry=registry) is None
