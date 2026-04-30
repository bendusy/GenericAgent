"""Fork-only: bridge between GeneraticAgent and the Feishu lark-cli.

Adds a `lark_cli` tool to GenericAgentHandler and exposes `run()` for slash
commands. Stays out of any upstream-maintained file except via two narrow
anchors documented in docs/FORK_ARCHITECTURE.md.
"""
import os
import subprocess
from dataclasses import dataclass
from typing import Optional

OVERFLOW_BYTES = 8 * 1024
HEAD_BYTES = 2000
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
    # Overflow path filled in Task 3
    head = out[:HEAD_BYTES] + "\n... [truncated, full content in doc] ...\n"
    return LarkResult(True, out, head, None, None)
