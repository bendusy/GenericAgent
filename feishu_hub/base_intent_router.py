"""IM 消息 → base record 触发路由。

设计：docs/superpowers/specs/2026-05-15-m4c-base-intent-router-design.md §1, §2
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from feishu_hub.base_config import BaseConfig, resolve_by_role, resolve_by_base_token
from feishu_hub.lark_cli import base_record_get
from feishu_hub.record_writer import append_product, cas_acquire_running
from feishu_hub.runner_registry import RunnerEntry, RunnerRegistry

_URL_RE = re.compile(
    r"https?://[\w.-]+/base/(\w+)\?[^\s]*?table=(tbl\w+)[^\s]*?record=(rec\w+)"
)
_SHORT_RE = re.compile(r"(\S[^\s]*?)\s+record:(rec\w+)")
_RUN_RE = re.compile(r"/run\s+(.+?)\s*$", re.MULTILINE | re.DOTALL)


def _parse_base_ref(text: str, configs: List[BaseConfig]) -> Optional[Tuple[str, str, str]]:
    """Returns (base_token, table_id, record_id) or None."""
    m = _URL_RE.search(text)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = _SHORT_RE.search(text.strip())
    if m:
        role, record_id = m.group(1).strip(), m.group(2)
        cfg = resolve_by_role(configs, role)
        if cfg:
            return cfg.base_token, cfg.table_id, record_id
    return None


def _resolve_bot(record: dict, cfg: BaseConfig) -> Optional[str]:
    """优先级：负责 AI > stage_to_bot[阶段]。两个字段都是 select 字段（飞书返回 list）。"""
    ai_list = record.get("负责 AI") or []
    if isinstance(ai_list, list) and ai_list:
        return ai_list[0]
    if isinstance(ai_list, str) and ai_list:
        return ai_list  # 兜底：万一是 plain string
    stage_list = record.get("阶段") or []
    stage = (stage_list[0] if isinstance(stage_list, list) and stage_list
             else stage_list if isinstance(stage_list, str) else None)
    if not stage:
        return None
    return cfg.stage_to_bot.get(stage)


def _extract_text(event: dict) -> Optional[str]:
    try:
        content = event["event"]["message"]["content"]
        if isinstance(content, str):
            content = json.loads(content)
        if isinstance(content, dict):
            return content.get("text", "")
        return ""
    except (KeyError, json.JSONDecodeError, TypeError):
        return None


def _dispatch_runner(bot_name: str, prompt: str,
                     on_pid: Callable[[int], None]) -> object:
    """Resolve ``bot_name`` → :class:`BotRole` → :class:`RunSpec` →
    :func:`dispatcher.runners.run`。

    bot_name 优先按 ``app_id`` 匹配，再退到 ``role``（base.yaml 里两种 form 都见过）。
    base 路径不创建飞书 task —— 产物直接落 base 行（由 record_writer 处理）。

    Returns:
        :class:`dispatcher.runners.RunResult`
    Raises:
        ValueError: 当 bots.yaml 找不到该 bot_name。
    """
    from feishu_hub import bot_role, config
    from feishu_hub.dispatcher import runners as _runners

    bots_path = config.root_dir() / "bots.yaml"
    bots = bot_role.load_bots(bots_path)
    bot = next(
        (b for b in bots if b.app_id == bot_name or b.role == bot_name),
        None,
    )
    if bot is None:
        raise ValueError(f"bot {bot_name!r} not found in bots.yaml")
    spec = _runners.RunSpec(
        runner=bot.runner, prompt=prompt, cwd=bot.default_cwd,
    )
    return _runners.run(spec, on_pid=on_pid)


def _build_prompt(bot: str, record: dict, record_id: str) -> str:
    return (
        f"你是 {bot} bot。当前 base 行 record_id={record_id}。\n"
        f"全字段：\n{json.dumps(record, ensure_ascii=False, indent=2)}\n\n"
        f"完成当前阶段工作；产物会被自动 append 到「产物」字段。"
    )


def try_handle(event: dict, *, configs: List[BaseConfig],
               registry: RunnerRegistry,
               reply_fn: Callable[[str], None]) -> bool:
    """If IM message matches /run <base_ref>, consume and trigger; return True.

    Return False to let caller route the event to legacy R5 IM path.
    """
    text = _extract_text(event)
    if not text:
        return False
    m = _RUN_RE.search(text)
    if not m:
        return False
    base_ref = m.group(1).strip()

    parsed = _parse_base_ref(base_ref, configs)
    if not parsed:
        reply_fn("base_ref 无法解析。支持：① base 行链接 ② `{role} record:recXXX`")
        return True
    base_token, table_id, record_id = parsed

    cfg = resolve_by_base_token(configs, base_token)
    if not cfg:
        reply_fn(f"base_token {base_token} 未注册")
        return True

    if registry.lookup_by_record_id(record_id):
        reply_fn("该 record 已有 runner 在跑")
        return True

    rec = base_record_get(base_token=base_token, table_id=table_id, record_id=record_id)
    bot = _resolve_bot(rec, cfg)
    if not bot:
        stage_list = rec.get("阶段") or ["(空)"]
        stage = stage_list[0] if isinstance(stage_list, list) and stage_list else stage_list
        reply_fn(f"阶段「{stage}」未绑 bot")
        return True

    marker, status = cas_acquire_running(
        record_id=record_id, base_token=base_token, table_id=table_id,
    )
    if status == "non_idle":
        reply_fn("该行非 idle，手动改回 idle 再 /run")
        return True
    if status == "concurrent_conflict":
        reply_fn("并发冲突，本次放弃")
        return True

    msg = event["event"]["message"]
    chat_id = msg.get("chat_id", "")
    msg_id = msg.get("message_id", "")
    entry = RunnerEntry(
        task_guid=f"base-{record_id}",
        task_url=f"https://feishu.cn/base/{base_token}?table={table_id}&record={record_id}",
        runner_pid=0, bot_app_id="cli_local", chat_id=chat_id,
        source_message_id=msg_id,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        record_id=record_id, base_token=base_token, table_id=table_id,
    )
    registry.register(entry)
    append_product(record_id=record_id, text=f"--- {bot} 启动 ---",
                   base_token=base_token, table_id=table_id)

    def _on_pid(pid: int) -> None:
        # RunnerEntry is frozen — re-register with updated pid.
        updated = RunnerEntry(
            task_guid=entry.task_guid, task_url=entry.task_url,
            runner_pid=pid, bot_app_id=entry.bot_app_id, chat_id=entry.chat_id,
            source_message_id=entry.source_message_id, started_at=entry.started_at,
            record_id=entry.record_id, base_token=entry.base_token, table_id=entry.table_id,
        )
        registry.register(updated)

    _dispatch_runner(bot, _build_prompt(bot, rec, record_id), _on_pid)
    return True
