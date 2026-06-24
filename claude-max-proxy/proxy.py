#!/usr/bin/env python3
"""GA -> Claude Code shape proxy.

Local Anthropic-compatible proxy for GenericAgent NativeClaudeSession.
- Inbound: GA native Claude Messages request.
- Outbound: request normalized to Claude Code-like headers/body/tool names.
- Return: tool names/inputs remapped back to GA names for ga.py.

Privacy: captures are opt-in and redacted by default.
"""
import os, json, time, uuid, hashlib, re, subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    from flask import Flask, request, Response, jsonify
except Exception as e:
    raise SystemExit("Flask is required: python3 -m pip install flask requests") from e
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PORT = int(os.environ.get("PORT", "5678"))
UPSTREAM = os.environ.get("UPSTREAM", "https://api.anthropic.com").rstrip("/")
DRY_RUN = os.environ.get("DRY_RUN", "0").lower() in ("1", "true", "yes", "on")
SAVE_CAPTURE = os.environ.get("SAVE_CAPTURE", "1").lower() in ("1", "true", "yes", "on")
TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "600"))
SESSION_ID = os.environ.get("CC_SESSION_ID") or str(uuid.uuid4())
CCH_SEED = 0x6E52736AC806831E
USE_CLAUDE_MAX = os.environ.get("USE_CLAUDE_MAX", "1").lower() in ("1", "true", "yes", "on")
KEYCHAIN_SERVICE = os.environ.get("CLAUDE_KEYCHAIN_SERVICE", "Claude Code-credentials")


def _load_claude_max_token() -> Optional[str]:
    """Read a *non-expired* Claude Code OAuth access token from macOS Keychain.
    Returns None on any failure OR when the stored token is already expired —
    callers then fall back to passthrough instead of forwarding a dead token."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout.strip())
        tok = data.get("claudeAiOauth", {}).get("accessToken")
        exp = data.get("claudeAiOauth", {}).get("expiresAt")
        if tok and isinstance(exp, (int, float)) and exp / 1000 < time.time():
            print("[claude-max-proxy] WARN: keychain token expired; refresh via `claude` first", flush=True)
            return None
        return tok or None
    except Exception as e:
        print(f"[claude-max-proxy] keychain read failed: {e}", flush=True)
        return None


# Token cache with short TTL so a `claude` refresh is picked up within ~60s
# without restarting the proxy (critical when run persistently under launchd).
_TOKEN_CACHE: Dict[str, Any] = {"tok": None, "at": 0.0}
_TOKEN_TTL = float(os.environ.get("CLAUDE_TOKEN_TTL", "60"))


def _get_claude_max_token() -> Optional[str]:
    """Return a fresh OAuth token, re-reading Keychain at most once per TTL window."""
    if not USE_CLAUDE_MAX:
        return None
    now = time.time()
    if _TOKEN_CACHE["tok"] and now - _TOKEN_CACHE["at"] < _TOKEN_TTL:
        return _TOKEN_CACHE["tok"]
    tok = _load_claude_max_token()
    _TOKEN_CACHE["tok"], _TOKEN_CACHE["at"] = tok, now
    return tok


CLAUDE_MAX_TOKEN = _load_claude_max_token() if USE_CLAUDE_MAX else None

app = Flask(__name__)


def _load_json(path: Path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default

TOOL_MAP = _load_json(HERE / "tool_name_mapping.json", {})
REV_TOOL_MAP = {v: k for k, v in TOOL_MAP.items()}
# Legacy return-only aliases. Never exposed in outbound tool schema.
REV_TOOL_MAP.update({
    "exec": "code_run", "read": "file_read", "edit": "file_patch", "write": "file_write",
    "sessions_send": "ask_user", "sessions_run": "ask_user", "sessions_list": "ask_user", "sessions_history": "ask_user",
})

TRUE_CC = {}
for p in [HERE / "true_cc_capture.json", HERE / "cc_capture.json", HERE / "latest_cc_capture.json"]:
    if p.exists():
        TRUE_CC = _load_json(p, {})
        break


def _extract_body(capture: dict) -> dict:
    if not isinstance(capture, dict): return {}
    for key in ("json", "body", "request_body", "payload"):
        v = capture.get(key)
        if isinstance(v, dict): return v
        if isinstance(v, str):
            try: return json.loads(v)
            except Exception: pass
    return capture if "messages" in capture or "system" in capture else {}


def _extract_headers(capture: dict) -> dict:
    h = capture.get("headers") if isinstance(capture, dict) else None
    return h if isinstance(h, dict) else {}

TRUE_CC_BODY = _extract_body(TRUE_CC)
TRUE_CC_HEADERS = _extract_headers(TRUE_CC)


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(s in lk for s in ["authorization", "x-api-key", "api_key", "token", "cookie"]):
                out[k] = "<redacted>"
            elif lk in ("device_id", "account_uuid", "session_id"):
                out[k] = "<redacted>"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list): return [_redact(x) for x in obj]
    return obj


def _save_capture(prefix, data):
    if not SAVE_CAPTURE: return
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        fp = HERE / "captures" / f"{ts}-{prefix}.json"
        fp.write_text(json.dumps(_redact(data), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("[WARN] save_capture failed:", e, flush=True)


def _cch(body_bytes: bytes) -> str:
    # Stable SOP-compatible local implementation: seed bytes + final request body bytes -> sha256 hex.
    seed = CCH_SEED.to_bytes(8, "big", signed=False)
    return hashlib.sha256(seed + body_bytes).hexdigest()


def _parse_meta_user_id(body: dict) -> dict:
    meta = body.setdefault("metadata", {})
    uid = meta.get("user_id")
    if isinstance(uid, str):
        try: uid = json.loads(uid)
        except Exception: uid = {}
    if not isinstance(uid, dict): uid = {}
    return uid


def _sync_metadata(body: dict):
    uid = _parse_meta_user_id(body)
    true_uid = _parse_meta_user_id(TRUE_CC_BODY.copy()) if TRUE_CC_BODY else {}
    # device_id follows optional latest real CC capture; otherwise keep inbound or deterministic random in current process.
    uid["device_id"] = true_uid.get("device_id") or uid.get("device_id") or (uuid.uuid4().hex + uuid.uuid4().hex[:32])
    uid["account_uuid"] = uid.get("account_uuid") or str(uuid.uuid4())
    uid["session_id"] = SESSION_ID
    body.setdefault("metadata", {})["user_id"] = json.dumps(uid, separators=(",", ":"))


def _cc_system_blocks(inbound_system):
    cc_sys = TRUE_CC_BODY.get("system") if isinstance(TRUE_CC_BODY, dict) else None
    if isinstance(cc_sys, list) and len(cc_sys) >= 3:
        base = [dict(x) if isinstance(x, dict) else {"type":"text","text":str(x)} for x in cc_sys[:3]]
    else:
        base = [
            {"type":"text", "text":"You are Claude Code, Anthropic's official CLI for Claude.", "cache_control":{"type":"ephemeral"}},
            {"type":"text", "text":"You are operating inside a local GenericAgent environment.", "cache_control":{"type":"ephemeral"}},
            {"type":"text", "text":"Follow the user's instructions, use tools when needed, and preserve safety/privacy boundaries.", "cache_control":{"type":"ephemeral"}},
        ]
    ga_text = ""
    if isinstance(inbound_system, str): ga_text = inbound_system
    elif isinstance(inbound_system, list):
        parts=[]
        for b in inbound_system:
            if isinstance(b, dict): parts.append(str(b.get("text", "")))
            else: parts.append(str(b))
        ga_text = "\n".join([p for p in parts if p])
    if ga_text:
        base[2]["text"] = str(base[2].get("text", "")) + "\n\n" + ga_text
    return base


def _map_tool_input_to_cc(name, inp):
    if not isinstance(inp, dict): return inp
    out = dict(inp)
    if name == "Bash":
        if "script" in out and "command" not in out: out["command"] = out.pop("script")
        out.pop("type", None); out.pop("inline_eval", None)
    elif name in ("Read", "Edit", "Write"):
        if "path" in out and "file_path" not in out: out["file_path"] = out.pop("path")
        if name == "Edit":
            if "old_content" in out and "old_string" not in out: out["old_string"] = out.pop("old_content")
            if "new_content" in out and "new_string" not in out: out["new_string"] = out.pop("new_content")
        if name == "Write":
            if "new_content" in out and "content" not in out: out["content"] = out.pop("new_content")
    return out


def _map_tool_input_to_ga(name, inp):
    if not isinstance(inp, dict): return inp
    out = dict(inp)
    if name == "code_run":
        if "command" in out and "script" not in out: out["script"] = out.pop("command")
        out.setdefault("type", "powershell")
        out.setdefault("cwd", "./")
    elif name in ("file_read", "file_patch", "file_write"):
        if "file_path" in out and "path" not in out: out["path"] = out.pop("file_path")
        if name == "file_patch":
            if "old_string" in out and "old_content" not in out: out["old_content"] = out.pop("old_string")
            if "new_string" in out and "new_content" not in out: out["new_content"] = out.pop("new_string")
        if name == "file_write":
            if "content" in out and "new_content" not in out: out["new_content"] = out.pop("content")
    return out


def _remap_obj(obj: Any, direction: str):
    if isinstance(obj, list):
        return [_remap_obj(x, direction) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out = {k: _remap_obj(v, direction) for k, v in obj.items()}
    if direction == "to_cc":
        # outbound tool schema
        if "name" in out and out.get("input_schema") is not None and out["name"] in TOOL_MAP:
            out["name"] = TOOL_MAP[out["name"]]
        # outbound tool_use in prior assistant history
        if out.get("type") == "tool_use" and out.get("name") in TOOL_MAP:
            out["name"] = TOOL_MAP[out["name"]]
            out["input"] = _map_tool_input_to_cc(out["name"], out.get("input", {}))
    else:
        if out.get("type") == "tool_use" and out.get("name") in REV_TOOL_MAP:
            out["name"] = REV_TOOL_MAP[out["name"]]
            out["input"] = _map_tool_input_to_ga(out["name"], out.get("input", {}))
    return out


def _normalize_body(body: dict) -> dict:
    body = json.loads(json.dumps(body, ensure_ascii=False))
    inbound_system = body.get("system")
    body["model"] = os.environ.get("CC_MODEL", "claude-opus-4-8")
    body["max_tokens"] = int(os.environ.get("CC_MAX_TOKENS", "64000"))
    body.setdefault("stream", True)
    # CC-specific request shape; opt-in because OAuth Claude Max auth currently
    # rejects these fields with HTTP 400 unless paired with the right beta gates.
    if os.environ.get("CC_INJECT_THINKING", "0").lower() in ("1", "true", "yes", "on"):
        body["thinking"] = {"type": os.environ.get("CC_THINKING_TYPE", "adaptive")}
    if os.environ.get("CC_INJECT_CONTEXT_MGMT", "0").lower() in ("1", "true", "yes", "on"):
        body["context_management"] = {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}
    if os.environ.get("CC_INJECT_OUTPUT_CONFIG", "0").lower() in ("1", "true", "yes", "on"):
        body["output_config"] = {"effort": os.environ.get("CC_EFFORT", "max")}
    _sync_metadata(body)
    body["system"] = _cc_system_blocks(inbound_system)
    if isinstance(body.get("tools"), list): body["tools"] = _remap_obj(body["tools"], "to_cc")
    if isinstance(body.get("messages"), list): body["messages"] = _remap_obj(body["messages"], "to_cc")
    _cap_cache_control(body, limit=4)
    return body


def _cap_cache_control(body: dict, limit: int = 4) -> None:
    """Anthropic accepts <=4 blocks with cache_control. Drop excess from messages first
    (cheapest to recompute), keeping system/tools breakpoints intact."""
    blocks = []  # (priority, ref_dict) — lower priority dropped first
    def collect(o, prio):
        if isinstance(o, dict):
            if "cache_control" in o:
                blocks.append((prio, o))
            for v in o.values():
                collect(v, prio)
        elif isinstance(o, list):
            for v in o:
                collect(v, prio)
    collect(body.get("messages"), 0)  # drop these first
    collect(body.get("tools"), 1)
    collect(body.get("system"), 2)
    if len(blocks) <= limit:
        return
    blocks.sort(key=lambda x: x[0])
    drop = len(blocks) - limit
    for _, blk in blocks[:drop]:
        blk.pop("cache_control", None)


def _build_headers(in_headers, final_body_bytes: bytes):
    h = {
        "Content-Type": "application/json",
        "Accept": in_headers.get("accept", "text/event-stream"),
        "anthropic-version": in_headers.get("anthropic-version", "2023-06-01"),
        "anthropic-beta": in_headers.get("anthropic-beta", "claude-code-20250219,interleaved-thinking-2025-05-14,redact-thinking-2026-02-12,prompt-caching-scope-2026-01-05"),
        "anthropic-dangerous-direct-browser-access": "true",
        "user-agent": TRUE_CC_HEADERS.get("user-agent") or in_headers.get("user-agent") or "claude-cli/2.1.113 (external, cli)",
        "x-app": "cli",
        "X-Claude-Code-Session-Id": SESSION_ID,
        "x-claude-code-client-sha256": _cch(final_body_bytes),
        "X-Stainless-Lang": TRUE_CC_HEADERS.get("X-Stainless-Lang") or TRUE_CC_HEADERS.get("x-stainless-lang") or "js",
        "X-Stainless-OS": TRUE_CC_HEADERS.get("X-Stainless-OS") or TRUE_CC_HEADERS.get("x-stainless-os") or "MacOS",
        "X-Stainless-Arch": TRUE_CC_HEADERS.get("X-Stainless-Arch") or TRUE_CC_HEADERS.get("x-stainless-arch") or "arm64",
        "X-Stainless-Runtime": TRUE_CC_HEADERS.get("X-Stainless-Runtime") or TRUE_CC_HEADERS.get("x-stainless-runtime") or "node",
    }
    _tok = _get_claude_max_token()
    if _tok:
        # Override inbound credentials with Claude Max OAuth token from Keychain.
        h["authorization"] = f"Bearer {_tok}"
    else:
        if in_headers.get("x-api-key"): h["x-api-key"] = in_headers.get("x-api-key")
        if in_headers.get("authorization"): h["authorization"] = in_headers.get("authorization")
    return h


def _remap_json_bytes(data: bytes) -> bytes:
    try:
        obj = json.loads(data.decode("utf-8"))
        obj = _remap_obj(obj, "to_ga")
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except Exception:
        return data


def _remap_sse_lines(lines: Iterable[bytes]):
    for line in lines:
        if not line:
            yield b"\n"; continue
        if line.startswith(b"data: "):
            payload = line[6:]
            if payload.strip() != b"[DONE]":
                payload = _remap_json_bytes(payload)
            yield b"data: " + payload + b"\n"
        else:
            yield line + b"\n"

@app.get("/")
def home():
    return jsonify({"ok": True, "service": "claude-max-proxy", "port": PORT, "upstream": UPSTREAM, "dry_run": DRY_RUN, "session_id": SESSION_ID, "auth": "keychain-oauth" if _get_claude_max_token() else "passthrough"})

@app.post("/v1/messages")
@app.post("/messages")
def messages():
    inbound = request.get_json(force=True, silent=False)
    body = _normalize_body(inbound)
    body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = _build_headers(request.headers, body_bytes)
    _save_capture("outbound", {"headers": headers, "body": body})
    if DRY_RUN:
        # Return minimal valid non-stream or stream response to exercise local path without upstream.
        fake = {"id":"msg_dry_run","type":"message","role":"assistant","model":body.get("model"),"content":[{"type":"text","text":"DRY_RUN: proxy captured request."}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}
        if body.get("stream"):
            def gen():
                yield b'event: message_start\n'
                yield b'data: '+json.dumps({"type":"message_start","message":fake},separators=(",", ":")).encode()+b'\n\n'
                yield b'event: content_block_delta\n'
                yield b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"DRY_RUN: proxy captured request."}}\n\n'
                yield b'event: message_stop\n'
                yield b'data: {"type":"message_stop"}\n\n'
            return Response(gen(), mimetype="text/event-stream")
        return jsonify(fake)
    url = UPSTREAM + "/v1/messages"
    stream = bool(body.get("stream"))
    r = requests.post(url, headers=headers, data=body_bytes, stream=stream, timeout=TIMEOUT)
    resp_headers = {k:v for k,v in r.headers.items() if k.lower() not in ("content-encoding", "transfer-encoding", "connection")}
    if stream:
        return Response(_remap_sse_lines(r.iter_lines()), status=r.status_code, headers=resp_headers, mimetype="text/event-stream")
    return Response(_remap_json_bytes(r.content), status=r.status_code, headers=resp_headers, content_type=r.headers.get("content-type", "application/json"))

if __name__ == "__main__":
    print(f"[claude-max-proxy] port={PORT} upstream={UPSTREAM} dry_run={DRY_RUN} session={SESSION_ID} auth={'keychain-oauth' if CLAUDE_MAX_TOKEN else 'passthrough'}", flush=True)
    app.run(host="127.0.0.1", port=PORT, threaded=True)
