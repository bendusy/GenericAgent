"""dispatcher → 飞书多维表格写入器。

每条 ``dispatch.enqueued / started / completed / failed / timeout`` envelope
都插一行到 agent_tasks 表，飞书侧用看板视图按"状态"列实时可视化。

config 关键字段（``~/.feishu_hub/config.yaml``）:
  bitable:
    enabled: true
    base_token: "<your base app_token>"
    table_id:   "<your table_id, tblXXXXXX>"

为避免 dispatcher 主线程被 bitable HTTP 阻塞，每行用 fire-and-forget 线程写。
失败只打 warning 不影响主流程。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from typing import Any, Dict, Mapping, Optional

from feishu_hub import config as cfgmod
from feishu_hub.lark_cli import run_json, LarkCLIError


# event_type → 中文状态值
_STATE_MAP = {
    "dispatch.enqueued": "排队中",
    "dispatch.started":  "进行中",
    "dispatch.completed": "已完成",
    "dispatch.failed":   "失败",
    "dispatch.timeout":  "超时",
}

# 我们只把这些 event 写到 bitable，避免噪音
_WRITE_EVENTS = set(_STATE_MAP.keys())

# 列顺序与表 schema 对齐
_FIELD_ORDER = [
    "任务标题", "Agent", "状态", "触发源", "规则名",
    "创建时间", "完成时间", "耗时(s)",
    "摘要", "原始输入", "结果链接", "成本(¢)", "Tokens", "event_id",
]


def _ts_ms_now() -> int:
    return int(_dt.datetime.now().timestamp() * 1000)


def _bitable_cfg() -> Optional[Dict[str, Any]]:
    cfg = cfgmod.load(apply_env=False)
    bt = cfg.get("bitable") or {}
    if not bt.get("enabled"):
        return None
    if not (bt.get("base_token") and bt.get("table_id")):
        return None
    return bt


def _agent_from_envelope(env: Mapping[str, Any]) -> str:
    """envelope.command.argv[0] = runner 名 → 反推 agent。"""
    cmd = env.get("command") or {}
    argv = cmd.get("argv") or []
    if not argv:
        return "dispatcher"
    runner = argv[0]
    if runner == "cc_headless":
        return "cc"
    if runner == "codex_exec":
        return "codex"
    if runner == "gemini_headless":
        return "gemini"
    if runner == "noop":
        return "dispatcher"
    return runner


def _trigger_source(env: Mapping[str, Any]) -> str:
    """从 envelope 推 触发源 — 取 trigger.actor.session 形态等。dispatcher 自身 emit 没原始
    trigger 信息（trigger 是 parent event），用 actor 与 tag 简单推断。"""
    actor = env.get("actor") or {}
    if actor.get("trace_id"):
        # 链路深度 1 = 由 dispatcher 派出，parent 是 fsapp 飞书事件 / cron / hook
        return "feishu"  # 默认；rules 里 cron/hook 触发的 envelope.tags 应注明
    return "manual"


def _build_row(env: Mapping[str, Any]) -> Dict[str, Any]:
    et = env.get("event_type") or ""
    cmd = env.get("command") or {}
    io = env.get("io") or {}
    metrics = env.get("metrics") or {}
    tags = env.get("tags") or []
    rule_name = tags[1] if len(tags) >= 2 and tags[0] == "dispatch" else ""

    row: Dict[str, Any] = {
        "任务标题": (env.get("summary") or "")[:60] or f"<{et}>",
        "Agent": _agent_from_envelope(env),
        "状态": _STATE_MAP.get(et, ""),
        "触发源": _trigger_source(env),
        "规则名": rule_name,
        "创建时间": _ts_ms_now(),
        "摘要": (env.get("summary") or "")[:600],
        "event_id": env.get("event_id") or "",
    }
    if et in ("dispatch.completed", "dispatch.failed", "dispatch.timeout"):
        row["完成时间"] = _ts_ms_now()
        dur_ms = int(cmd.get("duration_ms") or 0)
        if dur_ms:
            row["耗时(s)"] = round(dur_ms / 1000, 1)
        if metrics.get("cost_cents") is not None:
            row["成本(¢)"] = metrics["cost_cents"]
        if metrics.get("tokens") is not None:
            row["Tokens"] = metrics["tokens"]
    return row


def _row_to_csv(row: Dict[str, Any]) -> list:
    return [row.get(k) for k in _FIELD_ORDER]


def _post_row(bt: Mapping[str, Any], row: Dict[str, Any]) -> None:
    """同步 POST 一行。失败时 stderr warning，不抛。"""
    try:
        run_json(
            [
                "base", "+record-batch-create",
                "--base-token", bt["base_token"],
                "--table-id", bt["table_id"],
                "--json", json.dumps(
                    {"fields": _FIELD_ORDER, "rows": [_row_to_csv(row)]},
                    ensure_ascii=False),
                "--as", "user",
            ],
            timeout=30,
        )
    except LarkCLIError as e:
        import sys
        sys.stderr.write(f"[bitable_writer] insert failed: {e.code} {e.msg[:120]}\n")


def write_event(env: Mapping[str, Any]) -> None:
    """对外入口：dispatcher.emit 时挂这个；只处理白名单事件，异步写。"""
    et = env.get("event_type") or ""
    if et not in _WRITE_EVENTS:
        return
    bt = _bitable_cfg()
    if not bt:
        return
    row = _build_row(env)
    threading.Thread(target=_post_row, args=(bt, row), daemon=True).start()


def wrap_emit(base_emit):
    """把现有 emit 包一层：先调原 emit，再异步写 bitable。"""
    def _emit(payload):
        try:
            base_emit(payload)
        finally:
            try:
                write_event(payload)
            except Exception as e:
                import sys
                sys.stderr.write(f"[bitable_writer] wrap_emit error: {e}\n")
    return _emit
