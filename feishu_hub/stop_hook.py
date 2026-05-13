"""Stop hook python 入口。从 shell 脚本调用，避免 shell 拼复杂 JSON。"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from feishu_hub import task_writer
from feishu_hub.lark_cli import LarkCLIError, run_json


def _send_im_fallback(receive_id: str, text: str, idempotency_key: str) -> None:
    """task_writer 失败时的兜底——纯文本 IM。任何异常都吞掉，确保 exit 0。"""
    try:
        run_json(
            [
                "im", "+messages-send",
                "--as", "bot",
                "--user-id", receive_id,
                "--text", text,
                "--idempotency-key", idempotency_key,
            ],
            timeout=15,
        )
    except Exception as e:
        sys.stderr.write(f"[feishu_hub.stop_hook] IM fallback failed: {e}\n")


def run(
    *,
    agent: str,
    session: str,
    cwd: str,
    summary: str,
    follower_open_id: Optional[str],
) -> int:
    """主入口。失败兜底不阻塞 agent。"""
    if not follower_open_id:
        return 0  # 没配通知对象 → 静默退出

    try:
        ref = task_writer.get_or_create_for_session(
            agent=agent,
            session=session,
            cwd=cwd,
            summary=f"[{agent}] @ {cwd.rsplit('/', 1)[-1]}",
            description=f"Agent {agent} working in {cwd}",
            follower_open_id=follower_open_id,
        )
        task_writer.append_steps(
            ref.guid,
            steps=[summary or "Agent stopped (no summary)"],
            idempotency_key=f"{agent}-{session}-step-{hash(summary) & 0xFFFFFFFF:x}",
        )
        return 0
    except LarkCLIError as e:
        sys.stderr.write(f"[feishu_hub.stop_hook] task path failed: {e}\n")
        # 降级到 IM text 兜底
        _send_im_fallback(
            follower_open_id,
            f"[{agent}] @ {cwd.rsplit('/', 1)[-1]}: {summary[:120]}",
            f"{agent}-stop-{session}-fallback",
        )
        return 0


def _main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--agent", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--summary", default="")
    p.add_argument("--follower-open-id", default="")
    args = p.parse_args()
    return run(
        agent=args.agent,
        session=args.session,
        cwd=args.cwd,
        summary=args.summary,
        follower_open_id=args.follower_open_id,
    )


if __name__ == "__main__":
    sys.exit(_main())
