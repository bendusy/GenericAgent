"""bot_runner — IM event 路由层（M3.C → M3.E）。

把一条来自 :mod:`feishu_hub.event_bridge` 的 envelope 喂给 :func:`handle_event`：

1. 用 :func:`feishu_hub.bot_role.event_matches_bot` 判定是否归我处理
2. 用 :func:`bot_role.extract_message_body` 去掉开头 ``@<alias>`` 拿正文
3. **M3.E**: 调 :func:`bot_relay_task.record_start` 建飞书 task + append "🚀 已收到" step
4. 按 ``bot.prompt_template`` 模板化 → 调 ``runner``（默认走 dispatcher.runners.run）
5. **M3.E**: 调 :func:`bot_relay_task.record_end` append 完成 / 超时 / 失败 step
6. 按 ``bot.reply_template`` 模板化 → 调 ``replier`` 在原 thread 内回复
7. **M3.E**: reply 文本末尾追加 ``📋 查看完整进度：<task_url>``（若 record_start 拿到 TaskRef）
8. 若 ``bot.next_bot_mention`` 非空，reply 末尾追加 ``\\n<next_bot_mention> 请接力。``

relay_task 任一调用异常都被吞，不阻塞 runner / reply 主路径。

runner / replier 双依赖都用关键字参数注入，便于单测 mock。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from . import bot_relay_task
from . import bot_role as br
from .dispatcher.runners import RunResult, RunSpec, run as default_run


_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotAction:
    bot_app_id: str
    chat_id: str
    source_message_id: str
    reply_message_id: Optional[str]
    runner_exit_code: int
    timed_out: bool


Runner = Callable[[RunSpec], RunResult]
Replier = Callable[..., Optional[str]]  # kw: message_id, text, thread, profile

# Reply 末尾追加 task URL 的模板（M3.E）
_TASK_URL_SUFFIX = "\n\n📋 查看完整进度：{url}"

# brief 截断（feishu task step 文案）
_MESSAGE_BRIEF_MAX = 80


def _default_runner(spec: RunSpec) -> RunResult:
    return default_run(spec)


def _default_replier(
    *, message_id: str, text: str, thread: bool, profile: Optional[str] = None,
) -> Optional[str]:
    # 延迟 import 防循环
    from . import lark_cli
    return lark_cli.im_messages_reply(
        message_id=message_id,
        text=text,
        reply_in_thread=thread,
        profile=profile,
    )


def _format(template: str, **kw: Any) -> str:
    """``str.format_map`` with missing-key tolerance（模板里出现未知占位不抛错）。"""
    class _SafeDict(dict):
        def __missing__(self, key):  # type: ignore[override]
            return "{" + key + "}"
    return template.format_map(_SafeDict(**kw))


def _compose_reply(bot: br.BotRole, result: RunResult) -> str:
    """按 ``bot.reply_template`` 渲染主体，再按需追加 ``next_bot_mention``。"""
    if result.timed_out:
        body = f"⚠️ runner 超时（{result.duration_ms} ms），未完成。"
    elif result.exit_code != 0:
        err = result.stderr_head or "(no stderr)"
        body = f"❌ runner failed (exit={result.exit_code}): {err}"
    else:
        body_text = result.final_text or result.stdout_head or "(empty)"
        template = bot.reply_template or "{result}"
        body = _format(template, result=body_text)

    if bot.next_bot_mention:
        body = f"{body}\n{bot.next_bot_mention} 请接力。"
    return body


def handle_event(
    event: Dict[str, Any],
    bot: br.BotRole,
    *,
    runner: Optional[Runner] = None,
    replier: Optional[Replier] = None,
) -> Optional[BotAction]:
    """处理一条事件；不匹配返回 ``None``，匹配返回 :class:`BotAction`。

    M3.E 调用顺序：record_start → runner → record_end → reply（含 task URL）。
    relay_task 任一步异常都不阻塞主路径（log 后继续）。
    """
    if not br.event_matches_bot(event, bot):
        return None

    runner = runner or _default_runner
    replier = replier or _default_replier

    body = br.extract_message_body(event, bot)

    # 1. record_start: 建 task + 起始 step（runner 跑前，user 飞书 task 详情页即可见 "已收到"）
    task_ref = None
    try:
        task_ref = bot_relay_task.record_start(
            bot=bot, event=event, message_brief=body[:_MESSAGE_BRIEF_MAX],
        )
    except Exception:
        _log.exception("record_start failed: bot=%s msg=%s",
                       bot.app_id, event.get("message_id"))

    # 2. runner
    prompt = _format(
        bot.prompt_template,
        message=body,
        sender=event.get("sender_id", ""),
        chat_id=event.get("chat_id", ""),
    )
    result = runner(RunSpec(
        runner=bot.runner,
        prompt=prompt,
        cwd=bot.default_cwd,
    ))

    # 3. record_end: append 完成 / 超时 / 失败 step
    action_seed = BotAction(
        bot_app_id=bot.app_id,
        chat_id=event.get("chat_id", ""),
        source_message_id=event["message_id"],
        reply_message_id=None,
        runner_exit_code=result.exit_code,
        timed_out=result.timed_out,
    )
    result_text_for_step = result.final_text or result.stdout_head or ""
    try:
        bot_relay_task.record_end(
            bot=bot, action=action_seed, result_text=result_text_for_step,
        )
    except Exception:
        _log.exception("record_end failed: bot=%s msg=%s",
                       bot.app_id, event.get("message_id"))

    # 4. reply: 主体（含 next_bot_mention）+ 可选 task URL 后缀
    reply_text = _compose_reply(bot, result)
    if task_ref is not None:
        reply_text = f"{reply_text}{_TASK_URL_SUFFIX.format(url=task_ref.url)}"
    reply_id = replier(
        message_id=event["message_id"],
        text=reply_text,
        thread=True,
        profile=bot.app_id,
    )
    return BotAction(
        bot_app_id=bot.app_id,
        chat_id=event.get("chat_id", ""),
        source_message_id=event["message_id"],
        reply_message_id=reply_id,
        runner_exit_code=result.exit_code,
        timed_out=result.timed_out,
    )
