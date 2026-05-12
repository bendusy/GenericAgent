"""dispatcher 主编排：event → match rules → trace/budget gates → run runner → emit result envelopes。

设计：``docs/FEISHU_HUB_DISPATCHER_DESIGN.md`` §3/§7/§8/§11。

本模块不做 IO：不读 rules.yaml、不 tail journal、不调 lark-cli。
所有外部状态（rules、runaway_tracker、budget_state）由调用方传入；
journal envelope 也通过回调 ``emit_event`` 接出，便于单测。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from feishu_hub import journal

from . import budget as budget_mod
from . import rules as rules_mod
from . import runners
from . import trace as trace_mod


# emit_event(payload_dict) — 调用方接管 journal 落盘
EmitFn = Callable[[Mapping[str, Any]], None]
# run_fn(spec, ctx) → RunResult
RunFn = Callable[[runners.RunSpec, Optional[trace_mod.TraceCtx]], runners.RunResult]


@dataclass
class DispatchContext:
    rules: Sequence[rules_mod.Rule]
    runaway: trace_mod.RunawayTracker
    budget_state: budget_mod.BudgetState
    emit: EmitFn
    run_fn: RunFn = runners.run
    max_depth: int = trace_mod.DEFAULT_MAX_DEPTH


# ---- 内部 helper --------------------------------------------------------

def _base_envelope(event_type: str, *, rule_name: str, runner: str,
                   ctx: Optional[trace_mod.TraceCtx]) -> Dict[str, Any]:
    base = {
        "event_type": event_type,
        "source": "dispatcher",
        "actor": journal.actor_from_env(),
        "tags": ["dispatch", rule_name],
        "command": {"argv": [runner]},
        "summary": None,
    }
    if ctx is not None:
        base["actor"].update(ctx.to_actor_fields())
    return base


def _refusal(emit: EmitFn, event_type: str, *, rule_name: str, runner: str,
             ctx: Optional[trace_mod.TraceCtx], reason: str) -> None:
    payload = _base_envelope(event_type, rule_name=rule_name, runner=runner, ctx=ctx)
    payload["summary"] = reason
    emit(payload)


# ---- 主入口 -------------------------------------------------------------

def dispatch_event(event: Mapping[str, Any], dctx: DispatchContext) -> int:
    """处理一条 envelope；返回真正执行的 dispatch 数（被拒/无命中返回 0）。

    Steps:
        1. 跳过自身事件（rules 内部也会过滤）
        2. matcher 找出所有命中 rule
        3. 对每条命中：
            a. 推导新 trace ctx（depth+1）
            b. check_depth；超限 → emit ``dispatch.depth_exceeded`` 跳过
            c. runaway.check；超限 → emit ``dispatch.runaway`` 跳过
            d. budget.check_or_raise；超限 → emit ``dispatch.budget_exceeded`` 跳过
            e. emit ``dispatch.enqueued`` + ``dispatch.started``
            f. run_fn(spec, ctx)
            g. budget.record；emit ``dispatch.completed/failed/timeout``
    """
    hits = rules_mod.matches(dctx.rules, event)
    if not hits:
        return 0

    parent_event_id = event.get("event_id")
    parent_ctx = trace_mod.from_event(event)
    executed = 0

    for match in hits:
        rule = match.rule
        spec = match.spec
        ctx = trace_mod.child(parent_ctx, parent_event_id=parent_event_id)

        # 1) depth gate — rule 内 budget.max_depth 优先，否则用全局
        rule_max_depth = int(rule.action.budget.get("max_depth",
                                                     dctx.max_depth))
        try:
            trace_mod.check_depth(ctx, max_depth=rule_max_depth)
        except trace_mod.DepthExceeded as e:
            _refusal(dctx.emit, "dispatch.depth_exceeded",
                     rule_name=rule.name, runner=spec.runner, ctx=ctx,
                     reason=str(e))
            continue

        # 2) runaway gate
        dctx.runaway.record(ctx.trace_id)
        try:
            dctx.runaway.check(ctx.trace_id)
        except trace_mod.RunawayDetected as e:
            _refusal(dctx.emit, "dispatch.runaway",
                     rule_name=rule.name, runner=spec.runner, ctx=ctx,
                     reason=str(e))
            continue

        # 3) budget gate（事前估 cost=0；事后 record 真实 cost）
        try:
            budget_mod.check_or_raise(
                dctx.budget_state, runner=spec.runner, rule_name=rule.name,
                rule_budget=rule.action.budget,
            )
        except budget_mod.BudgetExceeded as e:
            _refusal(dctx.emit, "dispatch.budget_exceeded",
                     rule_name=rule.name, runner=spec.runner, ctx=ctx,
                     reason=str(e))
            continue

        # 4) enqueued + started（同一刻；本模块单进程串行）
        dctx.emit({**_base_envelope("dispatch.enqueued", rule_name=rule.name,
                                     runner=spec.runner, ctx=ctx),
                   "summary": f"rule={rule.name} runner={spec.runner}"})
        dctx.emit({**_base_envelope("dispatch.started", rule_name=rule.name,
                                     runner=spec.runner, ctx=ctx),
                   "command": {"argv": [spec.runner], "duration_ms": 0}})

        # 5) run
        t0 = time.time()
        try:
            result = dctx.run_fn(spec, ctx)
        except Exception as e:                      # runner 自己抛了
            payload = _base_envelope("dispatch.failed", rule_name=rule.name,
                                      runner=spec.runner, ctx=ctx)
            payload["command"]["duration_ms"] = int((time.time() - t0) * 1000)
            payload["command"]["exit_code"] = -1
            payload["summary"] = f"runner exception: {e}"
            dctx.emit(payload)
            continue

        # 6) 记账
        budget_mod.record(
            dctx.budget_state, runner=spec.runner, rule_name=rule.name,
            cost_cents=result.cost_cents or 0,
        )

        # 7) 落 dispatch.completed/failed/timeout
        if result.timed_out:
            evt = "dispatch.timeout"
        elif result.exit_code == 0:
            evt = "dispatch.completed"
        else:
            evt = "dispatch.failed"
        payload = _base_envelope(evt, rule_name=rule.name,
                                  runner=spec.runner, ctx=ctx)
        payload["command"] = {
            "argv": [spec.runner],
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
        }
        payload["io"] = {
            "stdout_head": result.stdout_head,
            "stderr_head": result.stderr_head,
            "stdin_present": False,
            "tty": False,
        }
        payload["summary"] = (
            result.final_text[:200] if result.final_text else
            result.stderr_head[:200]
        )
        if result.cost_cents is not None:
            payload.setdefault("metrics", {})["cost_cents"] = result.cost_cents
        if result.tokens is not None:
            payload.setdefault("metrics", {})["tokens"] = result.tokens
        dctx.emit(payload)
        executed += 1

    return executed
