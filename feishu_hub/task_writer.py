"""task_writer — bot 身份创建飞书 Task + 追加执行步骤。

协同模型：Task 是跨 agent 可见的工作项；agent 执行步骤流通过
``task agent_task_step_info append_task_steps`` 实时写入，user 在
飞书 app 看到 agent 进展。

约束（lark-cli 1.0.28 + POC 已验证）：
- ``task.agent_task_step_info.append_task_steps`` 要求 ``--as bot``
- bot 必须是 task 创建者；user-created task 写 step 会 10403
- ``timestamp`` 字段在 1.0.28 序列化有 bug，必须省略（server 自动填）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional, Sequence

from feishu_hub.lark_cli import run_json


@dataclass(frozen=True)
class TaskRef:
    """飞书 Task 引用。"""
    guid: str
    url: str


def create_task(
    agent: str,
    cwd: str,
    summary: str,
    *,
    description: str = "",
    follower_open_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> TaskRef:
    """bot 身份建任务。``follower_open_id`` 给 user open_id 让 user 在飞书 UI 可见。"""
    argv: List[str] = [
        "task", "+create",
        "--as", "bot",
        "--summary", summary,
    ]
    if description:
        argv += ["--description", description]
    if follower_open_id:
        argv += ["--follower", follower_open_id]
    if idempotency_key:
        argv += ["--idempotency-key", idempotency_key]

    resp = run_json(argv, timeout=30)
    # task +create shortcut 返回 {ok, identity, data:{guid, url}}；run_json 会解析
    data = resp.get("data", resp) if isinstance(resp, dict) else {}
    return TaskRef(guid=data["guid"], url=data["url"])


def append_steps(
    task_guid: str,
    steps: Sequence[str],
    *,
    idempotency_key: Optional[str] = None,
) -> None:
    """bot 身份追加步骤。空 ``steps`` 直接返回不调 lark-cli。"""
    if not steps:
        return

    body = {
        "task_guid": task_guid,
        "task_steps": [{"content": s} for s in steps],
    }
    if idempotency_key:
        body["idempotent_key"] = idempotency_key

    argv: List[str] = [
        "task", "agent_task_step_info", "append_task_steps",
        "--as", "bot",
        "--data", json.dumps(body, ensure_ascii=False),
    ]
    run_json(argv, timeout=30)
