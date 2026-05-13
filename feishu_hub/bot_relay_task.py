"""bot_relay_task — 每次 bot 接力同步写飞书 Task 步骤（M3.C T6）。

把 :class:`feishu_hub.bot_runner.BotAction` 落成飞书 task 的 step：

- 每个 chat_id 在本机 cache 一份 ``~/.feishu_hub/state/m3c_chats/<chat_id>.json``，
  内含该 chat 的 task ``guid`` + ``url``
- 首次 ``record(...)`` 见到某 chat → 建 task；后续 append step 到同一 task
- step 含 role / brief / 状态标记（✅/⚠️ timeout/❌ exit≠0），管理者从飞书 inbox
  能完整看 reviewer → scribe → ... 接力链
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import task_writer
from .bot_role import BotRole
from .bot_runner import BotAction
from .task_writer import TaskRef


_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _state_root() -> Path:
    home = os.getenv("FEISHU_HUB_HOME")
    base = Path(home) if home else Path.home() / ".feishu_hub"
    d = base / "state" / "m3c_chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(chat_id: str) -> Path:
    safe = _SAFE.sub("_", chat_id) or "unknown"
    return _state_root() / f"{safe}.json"


def _load_cached(chat_id: str) -> Optional[TaskRef]:
    p = _cache_path(chat_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return TaskRef(guid=data["guid"], url=data["url"])
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _save_cached(chat_id: str, ref: TaskRef) -> None:
    _cache_path(chat_id).write_text(
        json.dumps({"guid": ref.guid, "url": ref.url}, ensure_ascii=False),
        encoding="utf-8",
    )


def _short_chat(chat_id: str) -> str:
    return chat_id[-8:] if chat_id else "?"


def _status_marker(action: BotAction) -> str:
    if action.timed_out:
        return "⚠️"
    if action.runner_exit_code != 0:
        return "❌"
    return "✅"


def _format_step(bot: BotRole, action: BotAction, message_brief: str) -> str:
    marker = _status_marker(action)
    suffix = ""
    if action.timed_out:
        suffix = " (timeout)"
    elif action.runner_exit_code != 0:
        suffix = f" (exit={action.runner_exit_code})"
    return f"{marker} [{bot.role}] {bot.app_id[-8:]} → {message_brief}{suffix}"


def _ensure_task(chat_id: str, bot: BotRole, writer_profile: Optional[str]) -> TaskRef:
    cached = _load_cached(chat_id)
    if cached:
        return cached
    ref = task_writer.create_task(
        agent="feishu_hub.bot_relay",
        cwd=bot.default_cwd,
        summary=f"M3.C 接力链 · {_short_chat(chat_id)}",
        description=f"IM chat_id={chat_id}（feishu_hub.bot_relay_task 自动建）",
        idempotency_key=f"m3c-relay-task:{chat_id}",
        profile=writer_profile,
    )
    _save_cached(chat_id, ref)
    return ref


def record(
    *,
    bot: BotRole,
    action: BotAction,
    message_brief: str,
) -> Optional[TaskRef]:
    """落 step；按 chat_id 复用同一飞书 task。``chat_id`` 缺失则 no-op。

    ``bot.relay_writer_app_id`` 非空时所有 lark-cli 调用都加 ``--profile``，
    把 relay_task 写到指定 bot 身份下——跨机/跨角色收敛到同一 task guid。
    （前提：本机有该 profile 且 token 有效。）
    """
    chat_id = action.chat_id
    if not chat_id:
        return None
    writer_profile = bot.relay_writer_app_id or None
    ref = _ensure_task(chat_id, bot, writer_profile)
    step = _format_step(bot, action, message_brief)
    idem = f"m3c-step:{action.source_message_id}:{bot.app_id}"
    task_writer.append_steps(ref.guid, [step], idempotency_key=idem,
                             profile=writer_profile)
    return ref
