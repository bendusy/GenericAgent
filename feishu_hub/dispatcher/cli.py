"""``python -m feishu_hub.dispatcher`` — 反向 hook 调度入口。

3 种触发模式：

- ``fire``：单次。stdin 接收一条 envelope JSON（或 hook 原始 JSON 转 envelope），
  立刻 dispatch。CC/Codex/Gemini Stop/SessionEnd hook 直触用这个。
- ``tail``：常驻。tail journal jsonl，对每条 envelope 走 ``dispatch_event``。
  launchd plist 拉起这个。
- ``replay``：调试。从已有 journal jsonl 按 event_id 选一条，重新跑一遍。

所有模式共享同一份 rules.yaml + RunawayTracker + BudgetState。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from feishu_hub import config as cfgmod
from feishu_hub import journal

from . import bitable_writer
from . import budget as budget_mod
from . import loop
from . import rules as rules_mod
from . import trace as trace_mod


DEFAULT_RULES_FILE = "rules.yaml"


# ---- 公用 -------------------------------------------------------------

def _rules_path(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg).expanduser()
    return cfgmod.root_dir() / DEFAULT_RULES_FILE


def _load_rules(arg: Optional[str]) -> List[rules_mod.Rule]:
    p = _rules_path(arg)
    if not p.exists():
        return []
    return rules_mod.load_rules_file(p)


def _build_emit() -> "loop.EmitFn":
    """默认 emit：写回 journal jsonl + 异步写 bitable（若启用）。"""
    def _emit(payload: Dict[str, Any]) -> None:
        try:
            journal.append(payload)
        except Exception as e:
            sys.stderr.write(f"[dispatcher] journal emit failed: {e}\n")
    return bitable_writer.wrap_emit(_emit)


def _build_dctx(rules_arg: Optional[str], *,
                max_depth: Optional[int] = None) -> loop.DispatchContext:
    rules_list = _load_rules(rules_arg)
    return loop.DispatchContext(
        rules=rules_list,
        runaway=trace_mod.RunawayTracker(),
        budget_state=budget_mod.load(),
        emit=_build_emit(),
        max_depth=max_depth or trace_mod.DEFAULT_MAX_DEPTH,
    )


def _save_budget(dctx: loop.DispatchContext) -> None:
    try:
        budget_mod.save(dctx.budget_state)
    except Exception as e:
        sys.stderr.write(f"[dispatcher] budget save failed: {e}\n")


# ---- fire -------------------------------------------------------------

def cmd_fire(args: argparse.Namespace) -> int:
    """从 stdin / 文件读一条 envelope，dispatch 一次。"""
    if args.event_file == "-" or not args.event_file:
        raw = sys.stdin.read()
    else:
        raw = Path(args.event_file).expanduser().read_text(encoding="utf-8")
    if not raw.strip():
        sys.stderr.write("[dispatcher fire] no input\n")
        return 2
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[dispatcher fire] invalid JSON: {e}\n")
        return 2
    if not isinstance(event, dict):
        sys.stderr.write("[dispatcher fire] event must be an object\n")
        return 2

    dctx = _build_dctx(args.rules, max_depth=args.max_depth)
    n = loop.dispatch_event(event, dctx)
    _save_budget(dctx)
    print(f"[dispatcher fire] dispatched {n}")
    return 0


# ---- tail -------------------------------------------------------------

def _today_journal_path() -> Path:
    return journal.journal_dir() / (_dt.date.today().isoformat() + ".jsonl")


def _checkpoint_path() -> Path:
    return cfgmod.root_dir() / "state" / "dispatcher.last_event_id"


def _load_checkpoint() -> Optional[str]:
    p = _checkpoint_path()
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    return text or None


def _save_checkpoint(event_id: str) -> None:
    """每条事件处理后落 last_event_id，重启从这条之后续读。"""
    p = _checkpoint_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(event_id, encoding="utf-8")
    os.replace(tmp, p)


def _replay_pending(fh, last_event_id: Optional[str]) -> bool:
    """文件已 open，定位到 last_event_id 之后；找不到则跳到 EOF。

    返回 True = 找到 checkpoint，已定位到其后；False = 没找到（新文件 / 已被切走）。
    """
    if last_event_id is None:
        fh.seek(0, os.SEEK_END)
        return False
    fh.seek(0)
    found = False
    while True:
        pos_before = fh.tell()
        line = fh.readline()
        if not line:
            break
        try:
            event = json.loads(line.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if event.get("event_id") == last_event_id:
            found = True
            break
    if not found:
        # 没在当前文件找到（很可能是 rotate 切走），从头处理整文件
        fh.seek(0)
    return found


def cmd_tail(args: argparse.Namespace) -> int:
    dctx = _build_dctx(args.rules, max_depth=args.max_depth)
    sleep_s = max(0.1, float(args.poll_interval))   # 防 0 占满 CPU

    current_path: Optional[Path] = None
    fh = None
    print(f"[dispatcher tail] watching {journal.journal_dir()}", flush=True)
    print(f"[dispatcher tail] checkpoint = {_load_checkpoint() or '(none)'}",
          flush=True)
    try:
        while True:
            today_path = _today_journal_path()
            if current_path != today_path:
                if fh is not None:
                    fh.close()
                if not today_path.exists():
                    today_path.parent.mkdir(parents=True, exist_ok=True)
                    today_path.touch()
                fh = today_path.open("r", encoding="utf-8")
                checkpoint = _load_checkpoint()
                catchup_found = _replay_pending(fh, checkpoint)
                current_path = today_path
                mode = ("resume" if catchup_found
                        else "fresh-tail" if checkpoint is None
                        else "fresh-rotate")
                print(f"[dispatcher tail] open {today_path} ({mode})",
                      flush=True)
            line = fh.readline()
            if not line:
                time.sleep(sleep_s)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                n = loop.dispatch_event(event, dctx)
                if n:
                    _save_budget(dctx)
            except Exception as e:
                sys.stderr.write(f"[dispatcher tail] dispatch failed: {e}\n")
            # 不论是否触发派活，都更新 checkpoint —— 表明这条已处理过
            eid = event.get("event_id")
            if eid:
                try:
                    _save_checkpoint(eid)
                except Exception as e:
                    sys.stderr.write(f"[dispatcher tail] checkpoint save failed: {e}\n")
    except KeyboardInterrupt:
        print("[dispatcher tail] stopping", flush=True)
        return 0
    finally:
        if fh is not None:
            fh.close()


# ---- replay -----------------------------------------------------------

def cmd_replay(args: argparse.Namespace) -> int:
    eid = args.event_id
    target_date: Optional[_dt.date] = None
    if args.date:
        target_date = _dt.date.fromisoformat(args.date)
    # 默认搜近 30 天
    dates: List[_dt.date] = []
    today = _dt.date.today()
    if target_date:
        dates.append(target_date)
    else:
        for delta in range(30):
            dates.append(today - _dt.timedelta(days=delta))
    found = None
    for d in dates:
        for r in journal.read_day(date=d):
            if r.get("event_id") == eid:
                found = r
                break
        if found:
            break
    if not found:
        sys.stderr.write(f"[dispatcher replay] event_id {eid} not found\n")
        return 2
    dctx = _build_dctx(args.rules, max_depth=args.max_depth)
    n = loop.dispatch_event(found, dctx)
    _save_budget(dctx)
    print(f"[dispatcher replay] dispatched {n}")
    return 0


# ---- test-rule（dry run）---------------------------------------------

def cmd_test_rule(args: argparse.Namespace) -> int:
    rs = _load_rules(args.rules)
    raw = (sys.stdin.read() if args.event_file == "-"
           else Path(args.event_file).read_text(encoding="utf-8"))
    event = json.loads(raw)
    matches = rules_mod.matches(rs, event)
    if not matches:
        print("[dispatcher test-rule] no match")
        return 0
    for m in matches:
        print(f"  rule={m.rule.name} runner={m.spec.runner}")
        print(f"  prompt={m.spec.prompt!r}")
    return 0


# ---- parser -----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="feishu_hub.dispatcher")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--rules", help="rules.yaml 路径；默认 $FEISHU_HUB_HOME/rules.yaml")
    common.add_argument("--max-depth", type=int,
                        help="trace 链深度上限（默认 3）")

    p_fire = sub.add_parser("fire", parents=[common],
                            help="单条 envelope 触发（hook 直触用）")
    p_fire.add_argument("--event-file", default="-",
                        help='"-" 表示 stdin（默认）')
    p_fire.set_defaults(func=cmd_fire)

    p_tail = sub.add_parser("tail", parents=[common],
                            help="常驻 tail journal jsonl（launchd 拉起用）")
    p_tail.add_argument("--poll-interval", default="0.5",
                        help="无新行时 sleep 秒数（默认 0.5）")
    p_tail.set_defaults(func=cmd_tail)

    p_replay = sub.add_parser("replay", parents=[common],
                              help="按 event_id 从 journal 重派")
    p_replay.add_argument("event_id")
    p_replay.add_argument("--date", help="YYYY-MM-DD，缩小搜索（默认搜近 30 天）")
    p_replay.set_defaults(func=cmd_replay)

    p_tr = sub.add_parser("test-rule", parents=[common],
                          help="干 run：只匹配规则不真派")
    p_tr.add_argument("--event-file", default="-")
    p_tr.set_defaults(func=cmd_test_rule)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
