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
    "  /bbs <任务内容>           单条任务\n"
    "  /bbs fan <t1> | <t2> ...  并行拆分（| 或 ; 分隔，逐条独立发布）\n"
    "  /bbs list [N]             看最近 N 条（默认 10）\n"
    "  /bbs poll <since_id>      拉 since_id 之后的新帖\n"
    "  /bbs workers              看 worker 在线情况\n"
    "  /bbs status               看身份/配置/最近 5 条"
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
        # Pull workers + recent posts for a one-glance overview.
        authors_r = client._request("GET", "/authors")
        count_r = client._request("GET", "/count")
        recent_r = client.list_posts(limit=5)

        lines = [
            f"BBS: {settings.base_url}",
            f"调度员: {_DISPATCHER_NAME} (last_id={ident.data.get('last_id', 0)})",
        ]
        if count_r.ok:
            lines.append(f"总帖数: {count_r.data.get('total', '?')}")
        if authors_r.ok and authors_r.data:
            others = [a for a in authors_r.data if a != _DISPATCHER_NAME]
            lines.append(f"参与者({len(authors_r.data)}): " + ", ".join(authors_r.data))
            if not others:
                lines.append("⚠️  尚无 worker 在线，启动: python3 agentmain.py --reflect reflect/agent_team_worker_robust.py")
        if recent_r.ok and recent_r.data:
            lines.append("\n最近 5 条:")
            lines.extend(_format_post(p) for p in recent_r.data)
        elif recent_r.ok:
            lines.append("（暂无帖子）")
        send("\n".join(lines))
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

    if sub == "fan":
        rest = args[len("fan"):].strip()
        if not rest:
            send("用法：/bbs fan <任务1> | <任务2> | <任务3>\n（也支持用 ; 或换行分隔）")
            return True
        # split on | or ; or newline; allow worker hints like "@worker-a: 内容"
        import re
        parts = [p.strip() for p in re.split(r"[|;\n]+", rest) if p.strip()]
        if len(parts) <= 1:
            send("⚠️  没检测到分隔符（|;换行），请用 / bbs <任务> 发单条；或用 | 分隔多条")
            return True
        ok_ids, failed = [], []
        for p in parts:
            r = client.post(_DISPATCHER_NAME, p)
            if r.ok:
                ok_ids.append(r.data.get("id"))
            else:
                failed.append((p[:40], r.error))
        msg = f"✅ 已拆为 {len(parts)} 个任务，发布成功 #{ok_ids}"
        if failed:
            msg += "\n❌ 失败：\n" + "\n".join(f"  «{t}…» → {e}" for t, e in failed)
        send(msg)
        return True

    if sub == "workers":
        authors_r = client._request("GET", "/authors")
        if not authors_r.ok:
            send(f"❌ {authors_r.error}")
            return True
        authors = authors_r.data or []
        workers = [a for a in authors if a != _DISPATCHER_NAME]
        if not workers:
            send(
                "⚠️  没有 worker 注册过。启动 worker：\n"
                "  python3 agentmain.py --reflect reflect/agent_team_worker_robust.py\n"
                "或起多个：\n"
                "  GA_BBS_WORKER_NAME=worker-a python3 agentmain.py --reflect reflect/agent_team_worker_robust.py\n"
                "  GA_BBS_WORKER_NAME=worker-b python3 agentmain.py --reflect reflect/agent_team_worker_robust.py"
            )
            return True
        # 各 worker 最近一条活动时间
        lines = [f"已注册 worker ({len(workers)}):"]
        for w in workers:
            r = client.list_posts(author=w, limit=1)
            if r.ok and r.data:
                p = r.data[0]
                import time as _t
                ago = int(_t.time() - (p.get("created_at") or 0))
                ago_str = f"{ago}s 前" if ago < 60 else f"{ago // 60}m 前"
                lines.append(f"  • {w}  最近活动: #{p['id']} ({ago_str})")
            else:
                lines.append(f"  • {w}  （注册过但无发帖）")
        send("\n".join(lines))
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
