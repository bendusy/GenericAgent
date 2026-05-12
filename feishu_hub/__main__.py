"""``python -m feishu_hub <subcmd>`` 入口。

子命令：
- ``init``：建目录、解析真实 lark-cli 路径、写默认 config.yaml、部署 hook 脚本。
- ``shim``：以模块形式跑 shim（等价于 ``python -m feishu_hub.shim``，便于测试）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import config as cfgmod
from . import shim as shim_mod

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _resolve_lark_cli(shim_self: Optional[Path] = None) -> str:
    """用 `command -v` + readlink -f 拿真实 lark-cli 路径，拒绝指向 shim。"""
    try:
        out = subprocess.check_output(
            ["/usr/bin/env", "bash", "-lc", "command -v lark-cli"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = ""
    if not out:
        raise RuntimeError(
            "lark-cli not found on PATH; install it first: "
            "https://github.com/larksuite/cli"
        )
    real = os.path.realpath(out)
    if shim_self and os.path.realpath(shim_self) == real:
        raise RuntimeError(
            f"lark-cli on PATH already points at shim itself ({real}); "
            "rename the shim first"
        )
    if not os.path.exists(real):
        raise RuntimeError(f"lark-cli resolved to non-existent path: {real}")
    return real


def _ensure_dirs(root: Path) -> None:
    for sub in ("journal", "state/reports", "bin"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def _deploy_hook_script(root: Path) -> Path:
    src = TEMPLATES_DIR / "agent-stop-notify.sh"
    dst = root / "bin" / "agent-stop-notify.sh"
    shutil.copyfile(src, dst)
    mode = os.stat(dst).st_mode
    os.chmod(dst, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dst


def cmd_init(args: argparse.Namespace) -> int:
    from . import hooks_merge
    root = cfgmod.root_dir()
    _ensure_dirs(root)

    # 解析 real lark-cli
    real = ""
    try:
        real = _resolve_lark_cli()
    except RuntimeError as e:
        sys.stderr.write(f"[feishu_hub init] {e}\n")
        if not args.allow_missing_lark_cli:
            return 2

    # 读现有 config，没有则用默认
    cfg = cfgmod.load(apply_env=False)
    if real:
        cfg["shim"]["real_lark_cli"] = real

    # 交互式补全（M1a：仅在 --no-prompt 未给时询问）
    if not args.no_prompt:
        if not cfg["notify_receive_id"]:
            cfg["notify_receive_id"] = _prompt(
                "飞书通知接收方 open_id（可留空，后续填）: "
            ).strip()
        if not cfg["daily_report"]["root_folder_token"]:
            cfg["daily_report"]["root_folder_token"] = _prompt(
                "日报根文件夹 folder_token（可留空，后续填）: "
            ).strip()

    path = cfgmod.save(cfg)
    hook = _deploy_hook_script(root)

    hook_actions: List[str] = []
    if args.install_hooks:
        cc_path = Path(os.path.expanduser(args.cc_settings))
        codex_path = Path(os.path.expanduser(args.codex_hooks))
        try:
            hooks_merge.apply_template(
                template_name="claude_code_settings.json.tmpl",
                target_path=cc_path, hook_script=str(hook),
            )
            hook_actions.append(f"  merged → {cc_path}")
        except Exception as e:
            hook_actions.append(f"  CC merge skipped: {e}")
        try:
            hooks_merge.apply_template(
                template_name="codex_hooks.json.tmpl",
                target_path=codex_path, hook_script=str(hook),
            )
            hook_actions.append(f"  merged → {codex_path}")
        except Exception as e:
            hook_actions.append(f"  Codex merge skipped: {e}")

    print(f"[feishu_hub init] root         = {root}")
    print(f"[feishu_hub init] config       = {path}")
    print(f"[feishu_hub init] real_lark_cli= {cfg['shim']['real_lark_cli'] or '(not set)'}")
    print(f"[feishu_hub init] hook script  = {hook}")
    if hook_actions:
        print(f"[feishu_hub init] hooks install:")
        for line in hook_actions:
            print(line)
    print()
    if not args.install_hooks:
        print("下一步（手动，或重跑 init --install-hooks 自动合并）：")
        print("  1. CC settings.json 加入 Stop hook，调用 ~/.feishu_hub/bin/agent-stop-notify.sh")
        print("  2. ~/.codex/hooks.json 同上")
    print("  • export FEISHU_NOTIFY_TO=<open_id>（也可写进 shell rc）")
    return 0


def _prompt(text: str) -> str:
    try:
        return input(text)
    except (EOFError, KeyboardInterrupt):
        return ""


def cmd_shim(args: argparse.Namespace) -> int:
    sub = list(args.argv or [])
    if sub and sub[0] == "--":
        sub = sub[1:]
    return shim_mod.main(["lark-cli", *sub])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="feishu_hub")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="初始化 ~/.feishu_hub")
    p_init.add_argument("--no-prompt", action="store_true",
                        help="跳过交互式提示，所有空字段保留为空")
    p_init.add_argument("--allow-missing-lark-cli", action="store_true",
                        help="即使本机未装 lark-cli 也不报错（仅用于 CI/测试）")
    p_init.add_argument("--install-hooks", action="store_true",
                        help="自动 merge Stop hook 到 CC / Codex 配置")
    p_init.add_argument("--cc-settings", default="~/.claude/settings.json",
                        help="Claude Code settings 路径")
    p_init.add_argument("--codex-hooks", default="~/.codex/hooks.json",
                        help="Codex hooks 路径")
    p_init.set_defaults(func=cmd_init)

    p_shim = sub.add_parser("shim", help="以模块形式运行 shim")
    p_shim.add_argument("argv", nargs=argparse.REMAINDER)
    p_shim.set_defaults(func=cmd_shim)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
