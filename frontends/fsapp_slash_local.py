"""Fork-only: fast-path slash commands that bypass the LLM and call lark-cli directly.

Wired into frontends/fsapp.py via anchor #2 (see docs/FORK_ARCHITECTURE.md).
Adding a new shortcut: append to _MAP — key is the slash command (lowercase),
value is (args_builder, usage_hint).
"""
from frontends.lark_bridge import run

_MAP = {
    "/cal":  (lambda q: ["calendar", "+agenda"],
              "查看今天的日程，无需参数"),
    "/wiki": (lambda q: (["wiki", "+search", "--query", q] if q else None),
              "/wiki <关键词>"),
    "/mail": (lambda q: ["mail", "+inbox"],
              "查看收件箱，无需参数"),
    "/task": (lambda q: ["task", "+get-my-tasks"],
              "我的待办，无需参数"),
}


_BBS_USAGE = (
    "用法：\n"
    "  /bbs <任务内容>       发布任务到 BBS\n"
    "  /bbs list [N]         看最近 N 条（默认 10）\n"
    "  /bbs poll <since_id>  拉取 since_id 之后的新帖\n"
    "  /bbs status           看身份与配置"
)
_DISPATCHER_NAME = "feishu-dispatcher"


def _format_post(p):
    ts = p.get("created_at") or 0
    import time as _t
    when = _t.strftime("%H:%M:%S", _t.localtime(ts)) if ts else "?"
    content = (p.get("content") or "").strip()
    if len(content) > 280:
        content = content[:280] + "…"
    return f"#{p.get('id')} [{when}] @{p.get('author')}: {content}"


def _handle_bbs(args, send):
    if not args:
        send(_BBS_USAGE)
        return True

    try:
        from bbs import BBSClient, load_settings
    except Exception as e:
        send(f"❌ BBS 模块加载失败：{e}")
        return True

    settings = load_settings()
    client = BBSClient(settings)
    sub = (args.split(maxsplit=1)[0] if args else "").lower()

    if sub == "status":
        ident = client.ensure_identity(_DISPATCHER_NAME)
        if not ident.ok:
            send(f"❌ {ident.error}")
            return True
        send(
            f"BBS: {settings.base_url}\n"
            f"调度员: {_DISPATCHER_NAME} (last_id={ident.data.get('last_id', 0)})"
        )
        return True

    if sub == "list":
        rest = args.split(maxsplit=1)
        try:
            n = int(rest[1]) if len(rest) > 1 else 10
        except ValueError:
            n = 10
        n = max(1, min(50, n))
        r = client.list_posts(limit=n)
        if not r.ok:
            send(f"❌ {r.error}")
            return True
        if not r.data:
            send("（暂无帖子）")
            return True
        send("\n".join(_format_post(p) for p in r.data))
        return True

    if sub == "poll":
        rest = args.split(maxsplit=1)
        try:
            since = int(rest[1]) if len(rest) > 1 else 0
        except ValueError:
            send("用法：/bbs poll <since_id>")
            return True
        r = client.poll(since_id=since, limit=50)
        if not r.ok:
            send(f"❌ {r.error}")
            return True
        if not r.data:
            send(f"（since_id={since} 之后没有新帖）")
            return True
        last = max(p["id"] for p in r.data)
        client.update_last_id(_DISPATCHER_NAME, last)
        head = "\n".join(_format_post(p) for p in r.data[:10])
        more = f"\n…(还有 {len(r.data) - 10} 条)" if len(r.data) > 10 else ""
        send(f"{head}{more}\n\n下次 since_id={last}")
        return True

    # default: post the whole args as a task
    r = client.post(_DISPATCHER_NAME, args)
    if not r.ok:
        send(f"❌ 发布失败：{r.error}")
        return True
    send(f"✅ 已发布任务 #{r.data.get('id')} (作者: {r.data.get('author')})")
    return True


def dispatch_local_slash(cmd, args, send, ctx=None):
    """Return True if this fork-local slash command was handled, False otherwise.

    `cmd` is the lowercased slash token (e.g. '/cal'). `args` is the rest of the
    line as a single string. `send` is a callable taking one str argument.
    """
    cmd_lc = (cmd or "").lower()
    if cmd_lc == "/bbs":
        try:
            return _handle_bbs((args or "").strip(), send)
        except Exception as e:
            send(f"❌ /bbs 异常：{type(e).__name__}: {e}")
            return True

    entry = _MAP.get(cmd_lc)
    if not entry:
        return False
    builder, usage = entry
    cli_args = builder((args or "").strip())
    if cli_args is None:
        send(f"用法：{usage}")
        return True
    r = run(cli_args, title_hint=cmd[1:] if cmd else "lark")
    if not r.ok:
        send(f"❌ {r.error}")
        return True
    msg = r.head
    if r.doc_url:
        msg += f"\n\n📄 完整结果：{r.doc_url}"
    send(msg)
    return True
