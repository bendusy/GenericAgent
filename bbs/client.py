"""Robust BBS HTTP client.

Used by both the /bbs slash command (dispatcher side) and the worker reflect
script (consumer side). Never raises into caller; returns BBSResult.

Settings precedence (later wins):
  1. bbs/settings.json
  2. environment: GA_BBS_URL, GA_BBS_KEY, GA_BBS_IDENTITY_DIR
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(_DIR, "settings.json")
DEFAULT_IDENTITY_DIR = os.path.join(_DIR, "identities")
DEFAULT_TIMEOUT = 10.0
MAX_RETRIES = 3
INITIAL_BACKOFF = 0.5  # seconds


@dataclasses.dataclass(frozen=True)
class BBSResult:
    ok: bool
    data: Any = None
    error: str = ""
    status: int = 0


@dataclasses.dataclass(frozen=True)
class BBSSettings:
    base_url: str
    board_key: str
    identity_dir: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.board_key)


def load_settings() -> BBSSettings:
    cfg: dict = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception as e:
            print(f"[bbs] settings.json parse error: {e}", flush=True)
    base_url = (os.environ.get("GA_BBS_URL") or cfg.get("base_url") or "").rstrip("/")
    board_key = os.environ.get("GA_BBS_KEY") or cfg.get("board_key") or ""
    identity_dir = (
        os.environ.get("GA_BBS_IDENTITY_DIR")
        or cfg.get("identity_dir")
        or DEFAULT_IDENTITY_DIR
    )
    os.makedirs(identity_dir, exist_ok=True)
    return BBSSettings(base_url=base_url, board_key=board_key, identity_dir=identity_dir)


class BBSClient:
    def __init__(self, settings: Optional[BBSSettings] = None):
        self.settings = settings or load_settings()

    # ---------- low-level HTTP with retry ----------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        query: Optional[dict] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> BBSResult:
        s = self.settings
        if not s.configured:
            return BBSResult(False, error="BBS not configured (set GA_BBS_URL/GA_BBS_KEY or bbs/settings.json)")

        url = f"{s.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})

        body_bytes = None
        headers = {"X-API-Key": s.board_key}
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        last_err = ""
        for attempt in range(MAX_RETRIES):
            try:
                req = urllib.request.Request(url, data=body_bytes, method=method, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    status = resp.status
                    try:
                        return BBSResult(True, data=json.loads(raw) if raw else None, status=status)
                    except json.JSONDecodeError:
                        return BBSResult(True, data=raw.decode("utf-8", errors="replace"), status=status)
            except urllib.error.HTTPError as e:
                # 4xx is not retried (bad request / auth / not-found).
                msg = f"HTTP {e.code}: {e.reason}"
                if 400 <= e.code < 500:
                    return BBSResult(False, error=msg, status=e.code)
                last_err = msg
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = f"{type(e).__name__}: {e}"
            # transient: backoff with jitter
            if attempt < MAX_RETRIES - 1:
                time.sleep(INITIAL_BACKOFF * (2 ** attempt) + random.uniform(0, 0.3))
        return BBSResult(False, error=f"BBS unreachable after {MAX_RETRIES} attempts: {last_err}")

    # ---------- identity persistence ----------
    def _identity_path(self, name: str) -> str:
        # one file per (board_key, name); board_key first 8 chars to avoid leak
        key_prefix = self.settings.board_key[:8] or "default"
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return os.path.join(self.settings.identity_dir, f"{key_prefix}.{safe}.json")

    def _load_identity(self, name: str) -> Optional[dict]:
        p = self._identity_path(name)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _save_identity(self, name: str, ident: dict) -> None:
        p = self._identity_path(name)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(ident, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def ensure_identity(self, name: str) -> BBSResult:
        """Return BBSResult(ok=True, data={token,name,last_id}) or error."""
        cached = self._load_identity(name)
        if cached and cached.get("token"):
            return BBSResult(True, data=cached)
        r = self._request("POST", "/register", json_body={"name": name})
        if not r.ok:
            return r
        ident = {"name": name, "token": r.data.get("token"), "last_id": 0}
        self._save_identity(name, ident)
        return BBSResult(True, data=ident)

    def update_last_id(self, name: str, last_id: int) -> None:
        cached = self._load_identity(name) or {}
        if last_id > cached.get("last_id", 0):
            cached["last_id"] = last_id
            self._save_identity(name, cached)

    # ---------- high-level operations ----------
    def post(self, name: str, content: str) -> BBSResult:
        ident = self.ensure_identity(name)
        if not ident.ok:
            return ident
        return self._request(
            "POST",
            "/post",
            json_body={"token": ident.data["token"], "content": content},
        )

    def poll(self, since_id: int, limit: int = 50) -> BBSResult:
        return self._request("GET", "/poll", query={"since_id": since_id, "limit": limit})

    def list_posts(self, author: Optional[str] = None, limit: int = 20, offset: int = 0) -> BBSResult:
        return self._request("GET", "/posts", query={"author": author, "limit": limit, "offset": offset})

    def upload_file(self, name: str, file_path: str) -> BBSResult:
        """Multipart upload; falls back to inline content if file missing/unreadable."""
        ident = self.ensure_identity(name)
        if not ident.ok:
            return ident
        if not os.path.exists(file_path):
            return BBSResult(False, error=f"file not found: {file_path}")
        try:
            import mimetypes
            boundary = "----GA-BBS-" + os.urandom(8).hex()
            ctype, _ = mimetypes.guess_type(file_path)
            ctype = ctype or "application/octet-stream"
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            fname = os.path.basename(file_path)
            parts = [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="token"\r\n\r\n',
                ident.data["token"].encode(),
                b"\r\n",
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'.encode(),
                f"Content-Type: {ctype}\r\n\r\n".encode(),
                file_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
            body = b"".join(parts)
            url = f"{self.settings.base_url}/file/upload"
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "X-API-Key": self.settings.board_key,
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
            )
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT * 3) as resp:
                return BBSResult(True, data=json.loads(resp.read()), status=resp.status)
        except Exception as e:
            return BBSResult(False, error=f"upload failed: {type(e).__name__}: {e}")
