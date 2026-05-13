"""task_writer TDD：bot 创建 task + append steps，全 mock lark_cli。"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from feishu_hub.task_writer import (
    TaskRef,
    append_steps,
    create_task,
)


@patch("feishu_hub.task_writer.run_json")
def test_create_task_calls_lark_cli_as_bot(run_json):
    run_json.return_value = {
        "guid": "abc-123",
        "url": "https://applink.feishu.cn/client/todo/detail?guid=abc-123",
    }
    ref = create_task(
        agent="cc",
        cwd="/repo/foo",
        summary="[cc] @foo",
        description="cc Stop @ /repo/foo",
        follower_open_id="ou_xxx",
        idempotency_key="key-1",
    )
    assert isinstance(ref, TaskRef)
    assert ref.guid == "abc-123"
    assert ref.url.startswith("https://applink.feishu.cn/")

    argv = run_json.call_args.args[0]
    assert argv[0:2] == ["task", "+create"]
    assert "--as" in argv
    assert argv[argv.index("--as") + 1] == "bot"
    assert "--summary" in argv
    assert argv[argv.index("--summary") + 1] == "[cc] @foo"
    assert "--follower" in argv
    assert argv[argv.index("--follower") + 1] == "ou_xxx"
    assert "--idempotency-key" in argv
    assert argv[argv.index("--idempotency-key") + 1] == "key-1"


@patch("feishu_hub.task_writer.run_json")
def test_create_task_no_follower(run_json):
    run_json.return_value = {"guid": "g", "url": "u"}
    create_task(agent="cc", cwd="/r", summary="s")
    argv = run_json.call_args.args[0]
    assert "--follower" not in argv


@patch("feishu_hub.task_writer.run_json")
def test_append_steps_omits_timestamp(run_json):
    """lark-cli 1.0.28 bug：timestamp 字段必须省略。"""
    run_json.return_value = {"code": 0, "data": {}, "msg": ""}
    append_steps(
        task_guid="g-1",
        steps=["step a", "step b"],
        idempotency_key="ik-1",
    )

    argv = run_json.call_args.args[0]
    assert argv[0:3] == ["task", "agent_task_step_info", "append_task_steps"]
    assert "--as" in argv and argv[argv.index("--as") + 1] == "bot"
    assert "--data" in argv

    import json as _json
    data_str = argv[argv.index("--data") + 1]
    data = _json.loads(data_str)
    assert data["task_guid"] == "g-1"
    assert data["idempotent_key"] == "ik-1"
    assert len(data["task_steps"]) == 2
    assert data["task_steps"][0] == {"content": "step a"}
    assert data["task_steps"][1] == {"content": "step b"}
    # timestamp 必须不存在
    for step in data["task_steps"]:
        assert "timestamp" not in step


@patch("feishu_hub.task_writer.run_json")
def test_append_steps_empty_no_op(run_json):
    """空 steps 列表不应触发 lark-cli 调用。"""
    append_steps(task_guid="g", steps=[])
    run_json.assert_not_called()


@patch("feishu_hub.task_writer.run_json")
def test_create_task_propagates_lark_cli_error(run_json):
    from feishu_hub.lark_cli import LarkCLIError

    run_json.side_effect = LarkCLIError(
        code=10403, msg="unauthorized", argv=["task", "+create"]
    )
    with pytest.raises(LarkCLIError):
        create_task(agent="cc", cwd="/r", summary="s")


# --- T3: session cache tests ---
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from feishu_hub.task_writer import get_or_create_for_session


def test_get_or_create_first_call_creates(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))

    with patch("feishu_hub.task_writer.run_json") as run_json:
        run_json.return_value = {"guid": "g1", "url": "u1"}
        ref = get_or_create_for_session(
            agent="cc", session="sess-A",
            cwd="/r", summary="s", follower_open_id="ou_x",
        )
    assert ref.guid == "g1"
    # state 文件应写入
    cache_file = tmp_path / "state" / "session_tasks" / "cc-sess-A.json"
    assert cache_file.exists()
    import json
    cached = json.loads(cache_file.read_text())
    assert cached["task_guid"] == "g1"


def test_get_or_create_second_call_reuses(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    # 预置 cache
    cache_dir = tmp_path / "state" / "session_tasks"
    cache_dir.mkdir(parents=True)
    (cache_dir / "cc-sess-B.json").write_text(
        '{"task_guid":"existing","task_url":"u","created_at":"x","summary":"s"}'
    )

    with patch("feishu_hub.task_writer.run_json") as run_json:
        ref = get_or_create_for_session(
            agent="cc", session="sess-B",
            cwd="/r", summary="new summary",
        )
    # 不应调 lark-cli
    run_json.assert_not_called()
    assert ref.guid == "existing"


def test_get_or_create_session_sanitizes_path(tmp_path, monkeypatch):
    """agent / session 含特殊字符不应被注入文件路径。"""
    monkeypatch.setenv("FEISHU_HUB_HOME", str(tmp_path))
    with patch("feishu_hub.task_writer.run_json") as run_json:
        run_json.return_value = {"guid": "g", "url": "u"}
        get_or_create_for_session(
            agent="cc",
            session="../../etc/passwd",
            cwd="/r", summary="s",
        )
    # 应有文件被写在 session_tasks/ 内，不应跳出 sandbox
    cache_root = tmp_path / "state" / "session_tasks"
    files = list(cache_root.glob("*.json"))
    assert len(files) == 1
    # 文件名必须不含 / 或 .. 序列
    fname = files[0].name
    assert "/" not in fname
    assert ".." not in fname
