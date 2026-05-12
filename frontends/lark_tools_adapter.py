"""GA 侧适配器：把 feishu_hub 暴露为 ``do_daily_report`` / ``do_feishu_notify``。

设计 v2 §3 Layer A：本文件是 GA 与 feishu_hub 之间唯一的桥；任何 GA API 变更
（``StepOutcome`` / ``GenericAgentHandler``）只影响这一个文件，
不会传染到 feishu_hub 内部。

挂载方式：``import frontends.lark_tools_adapter`` 即触发 ``install()``，与
``frontends.lark_bridge`` 同套思路；侧挂、幂等、容错。
"""
from __future__ import annotations

from typing import Any, Dict

_StepOutcome = None  # 安装时填


def _outcome(payload: Any, next_prompt: str = "", *, exit_: bool = False):
    return _StepOutcome(payload, next_prompt=next_prompt,
                        should_exit=exit_) if _StepOutcome else payload


def _do_daily_report(self, args: Dict[str, Any], response):
    """生成或更新当日日报。

    args:
      date: "YYYY-MM-DD"  (可选；默认今日)
      note: str           (可选；附到第五段)
      force_new: bool     (可选；忽略 state 强制新建)
      no_notify: bool     (可选；跳过 IM 通知)
    """
    from feishu_hub import daily_report as dr
    import datetime as _dt
    try:
        date = _dt.date.fromisoformat(args["date"]) if args.get("date") else None
        rep = dr.generate(
            date=date,
            manual=args.get("note"),
            force_new=bool(args.get("force_new")),
            notify=not bool(args.get("no_notify")),
        )
    except Exception as e:
        return _outcome(f"[daily_report error] {e}")
    action = "新建" if rep.created else "更新"
    msg = (
        f"[daily_report] {action} {rep.title}\n"
        f"  url   = {rep.doc_url}\n"
        f"  token = {rep.doc_token}\n"
        f"  records={rep.record_count}, commits={rep.commit_count}"
    )
    return _outcome(msg)


def _do_feishu_notify(self, args: Dict[str, Any], response):
    """主动发飞书 IM。

    args:
      user_id: str        (必填；不填则用 config.notify_receive_id)
      text: str           (必填)
      idempotency_key: str (可选)
    """
    from feishu_hub import config as cfgmod, lark_cli
    cfg = cfgmod.load()
    user_id = args.get("user_id") or cfg.get("notify_receive_id") or ""
    text = args.get("text", "")
    if not user_id:
        return _outcome("[feishu_notify error] user_id missing and "
                        "notify_receive_id not configured")
    if not text:
        return _outcome("[feishu_notify error] text required")
    try:
        mid = lark_cli.im_send_text(
            user_id=user_id,
            text=text,
            idempotency_key=args.get("idempotency_key"),
        )
    except lark_cli.LarkCLIError as e:
        return _outcome(f"[feishu_notify error] {e.code}: {e.msg}")
    return _outcome(f"[feishu_notify] message_id={mid}")


def install() -> None:
    """幂等安装；GA 不可用时跳过（不抛异常，避免污染纯 feishu_hub 场景）。"""
    global _StepOutcome
    try:
        from ga import GenericAgentHandler  # type: ignore[import-not-found]
        from agent_loop import StepOutcome  # type: ignore[import-not-found]
    except Exception as e:
        print(f"[fork] lark_tools_adapter install skipped: {e}", flush=True)
        return
    _StepOutcome = StepOutcome
    if not hasattr(GenericAgentHandler, "do_daily_report"):
        GenericAgentHandler.do_daily_report = _do_daily_report
    if not hasattr(GenericAgentHandler, "do_feishu_notify"):
        GenericAgentHandler.do_feishu_notify = _do_feishu_notify


install()
