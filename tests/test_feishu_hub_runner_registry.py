import os
from pathlib import Path
import pytest

from feishu_hub.runner_registry import RunnerEntry, RunnerRegistry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    return RunnerRegistry()


def _entry(guid="t1", pid=12345, chat_id="oc_test"):
    return RunnerEntry(
        task_guid=guid, task_url=f"https://feishu.cn/task/{guid}",
        runner_pid=pid, bot_app_id="cli_x", chat_id=chat_id,
        source_message_id="om_x", started_at="2026-05-13T22:30:00+08:00",
    )


def test_register_then_lookup_returns_entry(registry):
    e = _entry()
    registry.register(e)
    assert registry.lookup("t1") == e


def test_lookup_unknown_returns_none(registry):
    assert registry.lookup("missing") is None


def test_unregister_removes_entry(registry):
    registry.register(_entry())
    registry.unregister("t1")
    assert registry.lookup("t1") is None


def test_unregister_unknown_is_noop(registry):
    registry.unregister("never-existed")  # no exception


def test_write_and_read_abort_sentinel(registry):
    registry.register(_entry())
    registry.write_abort_sentinel("t1", "/stop")
    assert registry.read_abort_sentinel("t1") == "/stop"


def test_read_abort_sentinel_returns_none_when_absent(registry):
    registry.register(_entry())
    assert registry.read_abort_sentinel("t1") is None


def test_unregister_also_cleans_sentinel(registry):
    registry.register(_entry())
    registry.write_abort_sentinel("t1", "/stop")
    registry.unregister("t1")
    assert registry.read_abort_sentinel("t1") is None


def test_lookup_by_chat_id_returns_match(registry):
    registry.register(_entry(guid="t1", pid=1, chat_id="oc_alpha"))
    registry.register(_entry(guid="t2", pid=2, chat_id="oc_beta"))
    e = registry.lookup_by_chat_id("oc_alpha")
    assert e is not None and e.task_guid == "t1"


def test_lookup_by_chat_id_returns_none(registry):
    registry.register(_entry(chat_id="oc_alpha"))
    assert registry.lookup_by_chat_id("oc_other") is None


def test_lookup_by_chat_id_returns_most_recent_when_multiple(registry):
    import dataclasses
    from feishu_hub.runner_registry import RunnerEntry
    old = _entry(guid="t_old", pid=1, chat_id="oc_x")
    registry.register(dataclasses.replace(old, started_at="2026-01-01T00:00:00+08:00"))
    new = _entry(guid="t_new", pid=2, chat_id="oc_x")
    registry.register(dataclasses.replace(new, started_at="2026-05-13T22:30:00+08:00"))
    e = registry.lookup_by_chat_id("oc_x")
    assert e.task_guid == "t_new"


def test_cleanup_orphans_removes_dead_pids(registry, monkeypatch):
    registry.register(_entry(guid="alive", pid=os.getpid()))
    registry.register(_entry(guid="dead", pid=999999))  # 假设 999999 不存在

    def fake_pid_alive(pid):
        return pid == os.getpid()

    monkeypatch.setattr("feishu_hub.runner_registry._pid_alive", fake_pid_alive)
    n = registry.cleanup_orphans()
    assert n == 1
    assert registry.lookup("alive") is not None
    assert registry.lookup("dead") is None


def test_write_and_read_adjust_sentinel(registry):
    registry.register(_entry())
    registry.write_adjust_sentinel("t1", "加点细节")
    assert registry.read_adjust_sentinel("t1") == "加点细节"


def test_read_adjust_sentinel_returns_none_when_absent(registry):
    registry.register(_entry())
    assert registry.read_adjust_sentinel("t1") is None


def test_unregister_also_cleans_adjust_sentinel(registry):
    registry.register(_entry())
    registry.write_adjust_sentinel("t1", "x")
    registry.write_abort_sentinel("t1", "/stop")
    registry.unregister("t1")
    assert registry.read_adjust_sentinel("t1") is None
    assert registry.read_abort_sentinel("t1") is None
