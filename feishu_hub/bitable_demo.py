"""bitable demo：一键往现有 base 里建 agent_tasks 表 + 10 行示例。

用法：
  # 先在飞书云空间建一个空 base，把 URL 里的 token 拿出来：
  BASE_TOKEN=<your_base_token> python3 -m feishu_hub.bitable_demo

字段定义参考飞书"超百万用户任务管理"模板。看板视图按"状态"列分组。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from typing import Any, Dict, List

from feishu_hub.lark_cli import run_json, LarkCLIError

TABLE_NAME = "agent_tasks"

# Feishu Bitable v3 field types（字符串 discriminator）
FIELDS: List[Dict[str, Any]] = [
    {"field_name": "任务标题", "type": "text"},
    {"field_name": "Agent", "type": "select", "options": [
        {"name": "ga"}, {"name": "cc"}, {"name": "codex"},
        {"name": "gemini"}, {"name": "dispatcher"}]},
    {"field_name": "状态", "type": "select", "options": [
        {"name": "排队中"}, {"name": "进行中"},
        {"name": "已完成"}, {"name": "失败"}, {"name": "超时"}]},
    {"field_name": "触发源", "type": "select", "options": [
        {"name": "feishu"}, {"name": "cron"},
        {"name": "hook"}, {"name": "manual"}]},
    {"field_name": "规则名", "type": "text"},
    {"field_name": "创建时间", "type": "datetime"},
    {"field_name": "完成时间", "type": "datetime"},
    {"field_name": "耗时(s)", "type": "number"},
    {"field_name": "摘要", "type": "text"},
    {"field_name": "原始输入", "type": "text"},
    {"field_name": "结果链接", "type": "link"},
    {"field_name": "成本(¢)", "type": "number"},
    {"field_name": "Tokens", "type": "number"},
    {"field_name": "event_id", "type": "text"},
]


def _ms(dt: _dt.datetime) -> int:
    return int(dt.timestamp() * 1000)


def _demo_rows() -> List[Dict[str, Any]]:
    """10 行典型场景：演示看板列分布 + 各 agent 类型。"""
    now = _dt.datetime.now()
    minute = _dt.timedelta(minutes=1)
    rows = [
        {"title": "/research 飞书CLI能力梳理", "agent": "cc", "状态": "已完成", "触发源": "feishu",
         "规则": "ga_slash_research_to_cc",
         "ts0": now - 6*minute, "ts1": now - 5*minute, "dur": 62,
         "summary": "整理 lark-cli 1.0.28 全量子命令与典型工作流。",
         "input": "/research 飞书 CLI 能做什么",
         "url": "https://my.feishu.cn/docx/JfktdZW4FoGHvAxDu9vccLTynSf",
         "cost": 26.5, "tokens": 5234, "eid": "01KRDXDD4VTS45"},
        {"title": "代码评审 ProjectX 最近 5 commit", "agent": "codex", "状态": "已完成", "触发源": "hook",
         "规则": "cc_session_end_review",
         "ts0": now - 30*minute, "ts1": now - 28*minute, "dur": 142,
         "summary": "发现 2 处潜在并发问题、1 处测试覆盖缺失，已建议补 fixture。",
         "input": "CC SessionEnd in /Users/ben/Projects/X",
         "url": "", "cost": 8.4, "tokens": 1820, "eid": "01KRDXDD5KZ8WP"},
        {"title": "每日日报 2026-05-12", "agent": "gemini", "状态": "已完成", "触发源": "cron",
         "规则": "daily_report.21:00",
         "ts0": now.replace(hour=21, minute=0, second=0) - _dt.timedelta(days=0),
         "ts1": now.replace(hour=21, minute=0, second=23),
         "dur": 23, "summary": "今日完成 7 项任务，主要在 dispatcher 反向 hook 与 bitable 联调。",
         "input": "auto-cron",
         "url": "https://my.feishu.cn/docx/E51bd1oeuojjBJxOH1acrj79nXY",
         "cost": 1.2, "tokens": 950, "eid": "01KRDYW07EEQQ4"},
        {"title": "/summarize 会议纪要", "agent": "gemini", "状态": "进行中", "触发源": "feishu",
         "规则": "ga_slash_summarize_to_gemini",
         "ts0": now - 2*minute, "ts1": None, "dur": None,
         "summary": "", "input": "/summarize <2026-Q2 review meeting notes...>",
         "url": "", "cost": None, "tokens": None, "eid": "01KRDZAB12CDEF"},
        {"title": "/ask 飞书 Bitable 限频多少", "agent": "codex", "状态": "进行中", "触发源": "feishu",
         "规则": "ga_slash_ask_to_codex",
         "ts0": now - 1*minute, "ts1": None, "dur": None,
         "summary": "", "input": "/ask 飞书多维表格 OpenAPI 写入 QPS 上限",
         "url": "", "cost": None, "tokens": None, "eid": "01KRDZB99ABCDE"},
        {"title": "GA 飞书消息回复", "agent": "codex", "状态": "排队中", "触发源": "feishu",
         "规则": "ga_feishu_any_message_to_codex",
         "ts0": now - 10*_dt.timedelta(seconds=1), "ts1": None, "dur": None,
         "summary": "", "input": "晚上吃啥",
         "url": "", "cost": None, "tokens": None, "eid": "01KRDZC123XYZ"},
        {"title": "/research GitHub Actions 安全实践", "agent": "cc", "状态": "失败", "触发源": "feishu",
         "规则": "ga_slash_research_to_cc",
         "ts0": now - 15*minute, "ts1": now - 14*minute, "dur": 67,
         "summary": "claude -p 401 unauthorized：CC 在 launchd env 里未登录",
         "input": "/research GitHub Actions Secret 泄漏防御 OWASP 2026",
         "url": "", "cost": 0, "tokens": 0, "eid": "01KRDXFAILXX"},
        {"title": "Codex 评审 GA agent_loop 改动", "agent": "codex", "状态": "超时", "触发源": "hook",
         "规则": "cc_session_end_review",
         "ts0": now - 22*minute, "ts1": now - 12*minute, "dur": 600,
         "summary": "评审耗时超 10 分钟 SIGKILL；建议拆分批次或调高 timeout_s。",
         "input": "review HEAD~5..HEAD in /Users/ben/Projects/GenericAgent",
         "url": "", "cost": 12.0, "tokens": 4100, "eid": "01KRDXTOUT0001"},
        {"title": "手动触发 ping 自检", "agent": "dispatcher", "状态": "已完成", "触发源": "manual",
         "规则": "ping_demo",
         "ts0": now - 90*_dt.timedelta(seconds=1), "ts1": now - 89*_dt.timedelta(seconds=1), "dur": 0,
         "summary": "pong: launchd daemon probe",
         "input": "dispatcher.ping", "url": "",
         "cost": 0, "tokens": 0, "eid": "01KRDSHA4D66YV"},
        {"title": "GA 长会话 turn 18 收尾", "agent": "ga", "状态": "已完成", "触发源": "feishu",
         "规则": "fsapp.task_complete",
         "ts0": now - 4*60*minute, "ts1": now - 3*60*minute - 30*minute, "dur": 1820,
         "summary": "axis ds2api healthcheck cross-app open_id 问题确认；已切 webhook 兜底。",
         "input": "都收到消息了",
         "url": "", "cost": 4.5, "tokens": 920, "eid": "01KRDQYBJC8XPX"},
    ]
    out: List[Dict[str, Any]] = []
    for r in rows:
        fields = {
            "任务标题": r["title"],
            "Agent": r["agent"],
            "状态": r["状态"],
            "触发源": r["触发源"],
            "规则名": r["规则"],
            "创建时间": _ms(r["ts0"]),
            "摘要": r["summary"],
            "原始输入": r["input"],
            "event_id": r["eid"],
        }
        if r.get("ts1") is not None:
            fields["完成时间"] = _ms(r["ts1"])
        if r.get("dur") is not None:
            fields["耗时(s)"] = r["dur"]
        if r.get("url"):
            fields["结果链接"] = r["url"]
        if r.get("cost") is not None:
            fields["成本(¢)"] = r["cost"]
        if r.get("tokens") is not None:
            fields["Tokens"] = r["tokens"]
        out.append({"fields": fields})
    return out


def main(base_token: str) -> None:
    print(f"[demo] base_token = {base_token}")

    # 1) 建表（不带 fields，先建空表）；已存在则复用
    table_id = None
    try:
        body = run_json([
            "base", "+table-create",
            "--base-token", base_token,
            "--name", TABLE_NAME,
            "--fields", json.dumps([{"field_name": "任务标题", "type": "text"}],
                                     ensure_ascii=False),
            "--as", "user",
        ])
        table_id = (body.get("data", {}).get("table_id")
                    or body.get("data", {}).get("table", {}).get("table_id"))
    except LarkCLIError as e:
        # 已存在：hint 里有 "(tblXXX)" 形式
        import re as _re
        m = _re.search(r"tbl[a-zA-Z0-9]+", e.msg)
        if m:
            table_id = m.group(0)
            print(f"[demo] reusing existing table_id = {table_id}")
        else:
            raise
    if not table_id:
        sys.exit(2)
    print(f"[demo] table_id    = {table_id}")

    # 2) 逐字段建（任务标题已经存在，跳过）
    for field in FIELDS[1:]:
        try:
            run_json([
                "base", "+field-create",
                "--base-token", base_token,
                "--table-id", table_id,
                "--json", json.dumps(field, ensure_ascii=False),
                "--as", "user",
            ])
        except LarkCLIError as e:
            print(f"[demo] field {field['field_name']!r} create failed: {e.msg[:120]}")

    # 3) 批量插入 10 行
    rows = _demo_rows()
    body = run_json([
        "base", "+record-batch-create",
        "--base-token", base_token,
        "--table-id", table_id,
        "--json", json.dumps({"records": rows}, ensure_ascii=False),
        "--as", "user",
    ], timeout=60)
    created = body.get("data", {}).get("records", [])
    print(f"[demo] inserted   = {len(created)} rows")

    print()
    print("✅ done. open in feishu：")
    print(f"   https://my.feishu.cn/base/{base_token}")
    print(f"   (切到看板视图按【状态】分组即可看到流水线效果)")


if __name__ == "__main__":
    token = os.environ.get("BASE_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not token:
        print("usage: BASE_TOKEN=<token> python3 -m feishu_hub.bitable_demo")
        sys.exit(1)
    main(token)
