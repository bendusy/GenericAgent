"""bot_bridge — 单 bot daemon orchestrator（M3.C T5）。

把 :func:`event_bridge.consume_im` 的事件流喂给 :func:`bot_runner.handle_event`，
单条事件异常不影响后续。一个 daemon = 一个 lark-cli profile = 一个 bot。
"""
from __future__ import annotations

import logging
from typing import Iterator

from . import bot_relay_task
from .bot_role import BotRole, extract_message_body
from .bot_runner import BotAction, handle_event
from .event_bridge import consume_im


_MESSAGE_BRIEF_LEN = 40


_log = logging.getLogger(__name__)


def run_bot(
    bot: BotRole,
    *,
    max_events: int = 0,
    timeout: str = "",
) -> Iterator[BotAction]:
    """长跑：消费 IM 事件流并按 ``bot`` 路由；yield 每次成功的 :class:`BotAction`。

    Args:
        bot: 当前 daemon 服务的角色（``app_id`` 同时是 lark-cli profile name）
        max_events: ``--max-events N``；0 = 不限制
        timeout: lark-cli ``--timeout`` 字面值；空 = 不限制
    """
    for event in consume_im(profile=bot.app_id, max_events=max_events, timeout=timeout):
        try:
            action = handle_event(event, bot)
        except Exception:
            _log.exception("handle_event failed: bot=%s msg=%s",
                           bot.app_id, event.get("message_id"))
            continue
        if action is None:
            continue
        try:
            brief = extract_message_body(event, bot)[:_MESSAGE_BRIEF_LEN]
            bot_relay_task.record(bot=bot, action=action, message_brief=brief)
        except Exception:
            _log.exception("bot_relay_task.record failed: bot=%s msg=%s",
                           bot.app_id, event.get("message_id"))
        yield action
