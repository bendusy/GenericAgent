"""Fork-only: bridge between GeneraticAgent and the Feishu lark-cli.

Adds a `lark_cli` tool to GenericAgentHandler and exposes `run()` for slash
commands. Stays out of any upstream-maintained file except via two narrow
anchors documented in docs/FORK_ARCHITECTURE.md.
"""
import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

OVERFLOW_BYTES = 8 * 1024
HEAD_CHARS = 2000
DEFAULT_TIMEOUT = 60
LARK_CLI = os.environ.get("LARK_CLI", "lark-cli")


@dataclass
class LarkResult:
    ok: bool
    stdout: str
    head: str
    doc_url: Optional[str]
    error: Optional[str]


def _run_raw(args, jq=None, timeout=DEFAULT_TIMEOUT):
    cmd = [LARK_CLI, *args]
    if "--format" not in args:
        cmd += ["--format", "json"]
    if jq:
        cmd += ["--jq", jq]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def _upload_overflow(content: str, title: str) -> Optional[str]:
    """Upload content as a Lark doc via `lark-cli docs +create` (v2, markdown via stdin).

    Returns the doc URL on success, None on any failure.
    """
    md = f"# {title}\n\n```\n{content}\n```\n"
    cmd = [LARK_CLI, "docs", "+create",
           "--api-version", "v2",
           "--title", title,
           "--content", "-",
           "--doc-format", "markdown"]
    try:
        p = subprocess.run(cmd, input=md, capture_output=True, text=True,
                           timeout=DEFAULT_TIMEOUT)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if p.returncode != 0:
        return None
    try:
        body = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None
    # Tolerate top-level / {"data": {...}} / {"data": {"document": {...}}} envelopes.
    if isinstance(body, dict):
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        document = data.get("document") if isinstance(data.get("document"), dict) else {}
        for src in (body, data, document):
            for key in ("url", "doc_url"):
                if src.get(key):
                    return src[key]
    return None


def run(args, jq=None, title_hint="lark-cli output", timeout=DEFAULT_TIMEOUT):
    try:
        rc, out, err = _run_raw(args, jq=jq, timeout=timeout)
    except subprocess.TimeoutExpired:
        return LarkResult(False, "", "", None, f"timeout after {timeout}s")
    except FileNotFoundError:
        return LarkResult(False, "", "", None, f"lark-cli not found at {LARK_CLI}")
    if rc != 0:
        return LarkResult(False, out, "", None, (err or out).strip()[:500])
    if len(out.encode("utf-8")) <= OVERFLOW_BYTES:
        return LarkResult(True, out, out, None, None)
    head = out[:HEAD_CHARS] + "\n... [truncated, full content in doc] ...\n"
    title = f"{title_hint} {time.strftime('%Y%m%d-%H%M%SZ', time.gmtime())}"
    doc_url = _upload_overflow(out, title)
    return LarkResult(True, out, head, doc_url, None)


# ---- Inject do_lark_cli onto GenericAgentHandler ----
_StepOutcome = None  # bound during install() to avoid import-order surprises


def _do_lark_cli(self, args, response):
    cli_args = args.get("args") or []
    if not cli_args:
        return _StepOutcome("[lark-cli error] empty args", next_prompt="")
    jq = args.get("jq")
    title = args.get("title", "lark-cli")
    try:
        timeout = int(args.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    r = run(cli_args, jq=jq, title_hint=title, timeout=timeout)
    if not r.ok:
        return _StepOutcome(f"[lark-cli error] {r.error}", next_prompt="")
    payload = r.head
    if r.doc_url:
        payload += f"\n\n[完整结果已存为飞书文档]({r.doc_url})"
    return _StepOutcome(payload, next_prompt="")


def install():
    """Idempotent: bind StepOutcome and attach do_lark_cli once."""
    global _StepOutcome
    try:
        from ga import GenericAgentHandler
        from agent_loop import StepOutcome
    except ImportError as e:
        print(f"[fork] lark_bridge install skipped: {e}", flush=True)
        return
    _StepOutcome = StepOutcome
    if not hasattr(GenericAgentHandler, "do_lark_cli"):
        GenericAgentHandler.do_lark_cli = _do_lark_cli


install()
