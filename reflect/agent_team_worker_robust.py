"""Fork-only: hardened BBS worker for `--reflect`.

Differences vs upstream agent_team_worker.py:
  - Reads config from bbs/settings.json (env override) instead of agent_team_setting.json
  - Persistent worker identity (token + last_id survive restarts) via bbs.client
  - Retry/backoff on transient HTTP errors; degraded mode when BBS is down
  - Exponential probe interval when BBS unreachable (60s → 120s → 240s, capped 600s)
  - Catches every exception in check() so the reflect loop never crashes
  - Worker name from GA_BBS_WORKER_NAME env, defaults to host+pid

Usage:
  python agentmain.py --reflect reflect/agent_team_worker_robust.py
"""

from __future__ import annotations

import os
import socket
import time
import traceback

# `reflect` runner imports module attributes directly; keep these names.
INTERVAL = 60
ONCE = False

try:
    from bbs import BBSClient, load_settings
except Exception as _e:  # pragma: no cover
    BBSClient = None  # type: ignore
    load_settings = None  # type: ignore
    print(f"[bbs-worker] failed to import bbs module: {_e}", flush=True)

_SETTINGS = load_settings() if load_settings else None
_CLIENT = BBSClient(_SETTINGS) if (BBSClient and _SETTINGS) else None

_WORKER_NAME = os.environ.get("GA_BBS_WORKER_NAME") or f"worker-{socket.gethostname().split('.')[0]}-{os.getpid()}"

_state = {
    "last_id": 0,
    "last_done": 0.0,
    "next_probe": 0.0,
    "backoff": 0,           # consecutive BBS failures
    "loaded_identity": False,
}

_MAX_BACKOFF_STEPS = 4  # 60 * 2^4 = 960 → cap below


def _load_identity_once():
    if _state["loaded_identity"] or not _CLIENT:
        return
    r = _CLIENT.ensure_identity(_WORKER_NAME)
    if r.ok:
        _state["last_id"] = max(_state["last_id"], int(r.data.get("last_id") or 0))
        _state["loaded_identity"] = True
        print(f"[bbs-worker] identity ready: {_WORKER_NAME} last_id={_state['last_id']}", flush=True)
    else:
        print(f"[bbs-worker] identity not yet available: {r.error}", flush=True)


def _next_probe_delay() -> float:
    # 60s base, doubles with consecutive failures, capped at 600s
    step = min(_state["backoff"], _MAX_BACKOFF_STEPS)
    return min(60.0 * (2 ** step), 600.0)


def check():
    """Called by reflect runner every INTERVAL seconds. Return prompt str or None."""
    if not _CLIENT or not _SETTINGS or not _SETTINGS.configured:
        return None

    try:
        # Keep waking up to track replies on a recently-finished task
        if _state["last_done"] > 0 and time.time() - _state["last_done"] < 120:
            return _prompt(reason="follow-up on recent task")

        # Backoff while BBS is unreachable
        now = time.time()
        if now < _state["next_probe"]:
            return None

        _load_identity_once()

        r = _CLIENT.poll(since_id=_state["last_id"], limit=10)
        if not r.ok:
            _state["backoff"] += 1
            _state["next_probe"] = now + _next_probe_delay()
            print(f"[bbs-worker] poll failed (backoff={_state['backoff']}): {r.error}", flush=True)
            return None

        _state["backoff"] = 0
        _state["next_probe"] = 0

        posts = r.data or []
        if not posts:
            return None

        # Filter out our own posts to avoid self-trigger loops
        new_posts = [p for p in posts if p.get("author") != _WORKER_NAME and p["id"] > _state["last_id"]]
        max_id = max(p["id"] for p in posts)
        # Persist last_id even if we skip (we've seen them)
        if max_id > _state["last_id"]:
            _state["last_id"] = max_id
            if _state["loaded_identity"]:
                _CLIENT.update_last_id(_WORKER_NAME, max_id)

        if not new_posts:
            return None

        return _prompt(reason=f"{len(new_posts)} new post(s)")
    except Exception as e:
        # Never crash the reflect loop
        print(f"[bbs-worker] check() exception: {type(e).__name__}: {e}\n{traceback.format_exc()}", flush=True)
        _state["backoff"] += 1
        _state["next_probe"] = time.time() + _next_probe_delay()
        return None


def on_done(result):
    _state["last_done"] = time.time()


def _prompt(reason: str = "") -> str:
    s = _SETTINGS
    return f"""[任务协作] 你是一个 BBS worker，名字: {_WORKER_NAME}
BBS: {s.base_url}  (key: {s.board_key[:6]}…)
唤醒原因: {reason}

操作流程：
1. GET /posts?limit=10&key=xxx 看最新帖子（since_id={_state['last_id']} 之前的可以忽略）
2. 找适合接的任务：被点名 @{_WORKER_NAME} 的优先；未点名但你能做的也可以接
3. 抢单：POST /post 内容写「[claim #帖id] {_WORKER_NAME} 接单」，最早抢到的那个执行
4. 执行完 POST /post 汇报结果；长结果用 POST /file/upload 上传后在帖子里贴 ref
5. 跟进：BBS 上对你结果的回复也要看，必要时再回帖
6. 不要重复接已被别人抢的单；不要自言自语（不要回复自己的帖子）
7. 不熟悉协议看 GET /readme?key=xxx

身份已持久化（{_WORKER_NAME}），重启不变。
"""
