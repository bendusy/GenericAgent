"""Per-turn guardrail for repeated/failed tool calls.

Ported from NousResearch/hermes-agent (agent/tool_guardrails.py), MIT.
Upstream copyright: (c) 2025 Nous Research.

The controller is intentionally side-effect free: it tracks tool-call
observations and returns decisions. Caller decides whether decisions become
warning guidance, synthetic tool results, or controlled turn halts.

GenericAgent defaults are tuned for our tool catalogue:
- idempotent (read-only)  : file_read, web_scan
- mutating (side effects) : code_run, file_patch, file_write, web_execute_js,
                            update_working_checkpoint, start_long_term_update
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


def _safe_json_loads(s: str | None) -> Any:
    if not s: return None
    try: return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError): return None


IDEMPOTENT_TOOL_NAMES = frozenset({
    "file_read", "web_scan",
})

MUTATING_TOOL_NAMES = frozenset({
    "code_run", "file_patch", "file_write", "web_execute_js",
    "update_working_checkpoint", "start_long_term_update",
})


@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """Thresholds for tool-call loop detection.

    Warnings are enabled by default and never prevent tool execution.
    Hard stops are explicit opt-in (set hard_stop_enabled=True).
    """

    warnings_enabled: bool = True
    hard_stop_enabled: bool = False
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5
    idempotent_tools: frozenset[str] = field(default_factory=lambda: IDEMPOTENT_TOOL_NAMES)
    mutating_tools: frozenset[str] = field(default_factory=lambda: MUTATING_TOOL_NAMES)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ToolCallGuardrailConfig":
        if not isinstance(data, Mapping): return cls()
        warn_after = data.get("warn_after") if isinstance(data.get("warn_after"), Mapping) else {}
        hard_stop_after = data.get("hard_stop_after") if isinstance(data.get("hard_stop_after"), Mapping) else {}
        d = cls()
        return cls(
            warnings_enabled=_as_bool(data.get("warnings_enabled"), d.warnings_enabled),
            hard_stop_enabled=_as_bool(data.get("hard_stop_enabled"), d.hard_stop_enabled),
            exact_failure_warn_after=_positive_int(warn_after.get("exact_failure", data.get("exact_failure_warn_after")), d.exact_failure_warn_after),
            same_tool_failure_warn_after=_positive_int(warn_after.get("same_tool_failure", data.get("same_tool_failure_warn_after")), d.same_tool_failure_warn_after),
            no_progress_warn_after=_positive_int(warn_after.get("idempotent_no_progress", data.get("no_progress_warn_after")), d.no_progress_warn_after),
            exact_failure_block_after=_positive_int(hard_stop_after.get("exact_failure", data.get("exact_failure_block_after")), d.exact_failure_block_after),
            same_tool_failure_halt_after=_positive_int(hard_stop_after.get("same_tool_failure", data.get("same_tool_failure_halt_after")), d.same_tool_failure_halt_after),
            no_progress_block_after=_positive_int(hard_stop_after.get("idempotent_no_progress", data.get("no_progress_block_after")), d.no_progress_block_after),
        )

    @classmethod
    def from_env(cls) -> "ToolCallGuardrailConfig":
        """Build config from GA_TOOLGUARD_* environment variables.

        Useful knobs:
          GA_TOOLGUARD=off         disable warnings (default: on)
          GA_TOOLGUARD_HARDSTOP=1  enable block/halt (default: off — warn-only)
        """
        import os
        return cls.from_mapping({
            "warnings_enabled": os.getenv("GA_TOOLGUARD", "on"),
            "hard_stop_enabled": os.getenv("GA_TOOLGUARD_HARDSTOP", "0"),
        })


@dataclass(frozen=True)
class ToolCallSignature:
    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> "ToolCallSignature":
        canonical = canonical_tool_args(args or {})
        return cls(tool_name=tool_name, args_hash=_sha256(canonical))

    def to_metadata(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "args_hash": self.args_hash}


@dataclass(frozen=True)
class ToolGuardrailDecision:
    action: str = "allow"  # allow | warn | block | halt
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None

    @property
    def allows_execution(self) -> bool: return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool: return self.action in {"block", "halt"}

    def to_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": self.action, "code": self.code, "message": self.message,
            "tool_name": self.tool_name, "count": self.count,
        }
        if self.signature is not None: data["signature"] = self.signature.to_metadata()
        return data


def canonical_tool_args(args: Mapping[str, Any]) -> str:
    if not isinstance(args, Mapping):
        raise TypeError(f"tool args must be a mapping, got {type(args).__name__}")
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def classify_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    """Detect whether a tool result represents failure.

    GA-specific: code_run + non-zero exit_code => failure.
    Fallback: presence of `"error"`/`"failed"` or "Error" prefix in first 500 chars.
    """
    if result is None: return False, ""
    if tool_name == "code_run":
        data = _safe_json_loads(result)
        if isinstance(data, dict):
            exit_code = data.get("exit_code")
            if exit_code is not None and exit_code != 0:
                return True, f" [exit {exit_code}]"
    lower = result[:500].lower()
    if '"error"' in lower or '"failed"' in lower or result.startswith("Error"):
        return True, " [error]"
    return False, ""


class ToolCallGuardrailController:
    """Per-session controller for repeated failed/non-progressing tool calls."""

    def __init__(self, config: ToolCallGuardrailConfig | None = None):
        self.config = config or ToolCallGuardrailConfig()
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._exact_failure_counts: dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[ToolCallSignature, tuple[str, int]] = {}
        self._halt_decision: ToolGuardrailDecision | None = None

    @property
    def halt_decision(self) -> ToolGuardrailDecision | None: return self._halt_decision

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> ToolGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))
        if not self.config.hard_stop_enabled:
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        exact_count = self._exact_failure_counts.get(signature, 0)
        if exact_count >= self.config.exact_failure_block_after:
            decision = ToolGuardrailDecision(
                action="block", code="repeated_exact_failure_block",
                message=(f"Blocked {tool_name}: the same tool call failed {exact_count} "
                         "times with identical arguments. Stop retrying it unchanged; "
                         "change strategy or explain the blocker."),
                tool_name=tool_name, count=exact_count, signature=signature,
            )
            self._halt_decision = decision
            return decision

        if self._is_idempotent(tool_name):
            record = self._no_progress.get(signature)
            if record is not None:
                _result_hash, repeat_count = record
                if repeat_count >= self.config.no_progress_block_after:
                    decision = ToolGuardrailDecision(
                        action="block", code="idempotent_no_progress_block",
                        message=(f"Blocked {tool_name}: this read-only call returned the same "
                                 f"result {repeat_count} times. Stop repeating it unchanged; "
                                 "use the result already provided or try a different query."),
                        tool_name=tool_name, count=repeat_count, signature=signature,
                    )
                    self._halt_decision = decision
                    return decision

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    def after_call(self, tool_name: str, args: Mapping[str, Any] | None, result: str | None,
                   *, failed: bool | None = None) -> ToolGuardrailDecision:
        args = _coerce_args(args)
        signature = ToolCallSignature.from_call(tool_name, args)
        if failed is None:
            failed, _ = classify_tool_failure(tool_name, result)

        if failed:
            exact_count = self._exact_failure_counts.get(signature, 0) + 1
            self._exact_failure_counts[signature] = exact_count
            self._no_progress.pop(signature, None)

            same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
            self._same_tool_failure_counts[tool_name] = same_count

            if self.config.hard_stop_enabled and same_count >= self.config.same_tool_failure_halt_after:
                decision = ToolGuardrailDecision(
                    action="halt", code="same_tool_failure_halt",
                    message=(f"Stopped {tool_name}: it failed {same_count} times. "
                             "Stop retrying the same failing tool path and choose a different approach."),
                    tool_name=tool_name, count=same_count, signature=signature,
                )
                self._halt_decision = decision
                return decision

            if self.config.warnings_enabled and exact_count >= self.config.exact_failure_warn_after:
                return ToolGuardrailDecision(
                    action="warn", code="repeated_exact_failure_warning",
                    message=(f"{tool_name} has failed {exact_count} times with identical arguments. "
                             "This looks like a loop; inspect the error and change strategy "
                             "instead of retrying it unchanged."),
                    tool_name=tool_name, count=exact_count, signature=signature,
                )

            if self.config.warnings_enabled and same_count >= self.config.same_tool_failure_warn_after:
                return ToolGuardrailDecision(
                    action="warn", code="same_tool_failure_warning",
                    message=(f"{tool_name} has failed {same_count} times. "
                             "This looks like a loop; change approach before retrying."),
                    tool_name=tool_name, count=same_count, signature=signature,
                )

            return ToolGuardrailDecision(tool_name=tool_name, count=exact_count, signature=signature)

        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        if not self._is_idempotent(tool_name):
            self._no_progress.pop(signature, None)
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        result_hash = _result_hash_func(result)
        previous = self._no_progress.get(signature)
        repeat_count = 1
        if previous is not None and previous[0] == result_hash:
            repeat_count = previous[1] + 1
        self._no_progress[signature] = (result_hash, repeat_count)

        if self.config.warnings_enabled and repeat_count >= self.config.no_progress_warn_after:
            return ToolGuardrailDecision(
                action="warn", code="idempotent_no_progress_warning",
                message=(f"{tool_name} returned the same result {repeat_count} times. "
                         "Use the result already provided or change the query instead of "
                         "repeating it unchanged."),
                tool_name=tool_name, count=repeat_count, signature=signature,
            )

        return ToolGuardrailDecision(tool_name=tool_name, count=repeat_count, signature=signature)

    def _is_idempotent(self, tool_name: str) -> bool:
        if tool_name in self.config.mutating_tools: return False
        return tool_name in self.config.idempotent_tools


def toolguard_synthetic_result(decision: ToolGuardrailDecision) -> str:
    return json.dumps({"error": decision.message, "guardrail": decision.to_metadata()}, ensure_ascii=False)


def append_toolguard_guidance(result: str, decision: ToolGuardrailDecision) -> str:
    if decision.action not in {"warn", "halt"} or not decision.message: return result
    label = "Tool loop hard stop" if decision.action == "halt" else "Tool loop warning"
    suffix = f"\n\n[{label}: {decision.code}; count={decision.count}; {decision.message}]"
    return (result or "") + suffix


def _coerce_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


def _result_hash_func(result: str | None) -> str:
    parsed = _safe_json_loads(result or "")
    if parsed is not None:
        try: canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        except TypeError: canonical = str(parsed)
    else: canonical = result or ""
    return _sha256(canonical)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None: return default
    if isinstance(value, bool): return value
    if isinstance(value, (int, float)): return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled"}: return True
        if lowered in {"0", "false", "no", "off", "disabled"}: return False
    return default


def _positive_int(value: Any, default: int) -> int:
    if value is None: return default
    try: parsed = int(value)
    except (TypeError, ValueError): return default
    return parsed if parsed >= 1 else default


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
