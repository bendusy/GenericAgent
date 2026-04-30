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


def dispatch_local_slash(cmd, args, send, ctx=None):
    """Return True if this fork-local slash command was handled, False otherwise.

    `cmd` is the lowercased slash token (e.g. '/cal'). `args` is the rest of the
    line as a single string. `send` is a callable taking one str argument.
    """
    entry = _MAP.get((cmd or "").lower())
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
