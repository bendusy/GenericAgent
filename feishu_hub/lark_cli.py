"""lark-cli 业务包裹：仅暴露已验证子命令，统一 JSON 解析与异常类型。

设计：``docs/FEISHU_OFFICE_HUB_DESIGN_V2.md`` §9.3。所有命令名与参数都在本机
``lark-cli --dry-run`` 验证过。

调用方式：始终通过 PATH 上的 ``lark-cli``（即部署后的 shim），让 journal 自动落盘；
单测可通过 ``binary=`` 参数或 ``FEISHU_HUB_LARK_CLI_BIN`` env 覆盖。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

DEFAULT_TIMEOUT = 30
DEFAULT_BIN = "lark-cli"
ENV_BIN = "FEISHU_HUB_LARK_CLI_BIN"
TRANSIENT_CODES = {99991663, 99991664}  # token 过期类，触发一次重试


class LarkCLIError(RuntimeError):
    """lark-cli 执行失败。``code`` 是业务码（int）或 -1（无法解析）。"""

    def __init__(self, code: int, msg: str, argv: Sequence[str],
                 stdout: str = "", stderr: str = "", retriable: bool = False):
        super().__init__(f"lark-cli failed: code={code} msg={msg}")
        self.code = code
        self.msg = msg
        self.argv = list(argv)
        self.stdout = stdout
        self.stderr = stderr
        self.retriable = retriable


@dataclass(frozen=True)
class DocInfo:
    doc_token: str
    url: Optional[str] = None


@dataclass(frozen=True)
class FolderEntry:
    name: str
    token: str
    type: str  # "folder" / "docx" / ...


def _binary() -> str:
    return os.getenv(ENV_BIN) or DEFAULT_BIN


def run_json(
    argv: Sequence[str],
    *,
    stdin: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    jq: Optional[str] = None,
    binary: Optional[str] = None,
    retries: int = 1,
) -> Any:
    """运行 lark-cli，强制 ``--format json``，解析 stdout。

    Returns
    -------
    解析后的 JSON 对象（dict / list / ...）。``--jq`` 提供时直接返回字符串去除两端
    空白后的结果（飞书侧 ``--jq`` 输出未必是 JSON）。
    """
    bin_ = binary or _binary()
    # lark-cli 多数子命令默认就输出 JSON 且不接受 --format flag（如 im +messages-send /
    # docs +create）；仅当调用方显式在 argv 里给了 --format 才透传。
    full = [bin_, *list(argv)]
    if jq:
        full += ["--jq", jq]

    last_err: Optional[LarkCLIError] = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(
                full,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise LarkCLIError(-1, f"timeout after {timeout}s", full,
                               retriable=True) from e
        except FileNotFoundError as e:
            raise LarkCLIError(-1, f"binary not found: {bin_}", full) from e

        if proc.returncode == 0:
            return _parse_output(proc.stdout, jq=jq, argv=full,
                                 stderr=proc.stderr)

        err = _parse_error(proc.stdout, proc.stderr, proc.returncode, full)
        if err.retriable and attempt < retries:
            last_err = err
            time.sleep(0.2 * (attempt + 1))
            continue
        raise err
    # 不会走到这里，但类型完整
    assert last_err is not None  # pragma: no cover
    raise last_err


def _parse_output(stdout: str, *, jq: Optional[str], argv: Sequence[str],
                  stderr: str) -> Any:
    if jq:
        return stdout.strip()
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LarkCLIError(-1, f"non-JSON stdout: {e}", argv,
                           stdout=stdout, stderr=stderr) from e


def _parse_error(stdout: str, stderr: str, returncode: int,
                 argv: Sequence[str]) -> LarkCLIError:
    code: int = returncode
    msg = (stderr or stdout or "").strip()[:500]
    text = stdout.strip()
    if text.startswith("{"):
        try:
            body = json.loads(text)
            if isinstance(body, dict):
                if "code" in body and isinstance(body["code"], int):
                    code = body["code"]
                if "msg" in body and isinstance(body["msg"], str):
                    msg = body["msg"]
        except json.JSONDecodeError:
            pass
    retriable = code in TRANSIENT_CODES or returncode in (124,)  # 124: timeout
    return LarkCLIError(code, msg, argv, stdout=stdout, stderr=stderr,
                        retriable=retriable)


# ---- 已验证子命令 ---------------------------------------------------------

def im_send_text(
    *,
    user_id: str,
    text: str,
    idempotency_key: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    binary: Optional[str] = None,
) -> Optional[str]:
    """``im +messages-send --user-id --text --idempotency-key``，返回 ``message_id``。"""
    argv: List[str] = ["im", "+messages-send", "--user-id", user_id, "--text", text]
    if idempotency_key:
        argv += ["--idempotency-key", idempotency_key]
    body = run_json(argv, timeout=timeout, binary=binary)
    return _pluck(body, ("data", "message_id")) or _pluck(body, ("message_id",))


def docs_create_v2(
    *,
    parent_token: str,
    markdown: str,
    title: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    binary: Optional[str] = None,
) -> DocInfo:
    """创建 docx 并移动到目标文件夹（如果 ``parent_token`` 给了）。

    lark-cli 1.0.28 实测：``docs +create --folder-token`` 仅是 hint，**不会**真的把
    文档放进目标文件夹（请求 body 不含 folder 字段，doc 默认落用户根目录）。
    解决：create 之后再调 ``drive +move`` 把 docx 移过去；title 用
    ``docs +update --new-title`` 设置。
    """
    # 1) 创建（lark-cli 1.0.28 的 --title / --folder-token 被静默忽略，所以这里
    #    只用 content 创建，下面再 move + rename）
    argv = [
        "docs", "+create",
        "--api-version", "v2",
        "--content", "-",
        "--doc-format", "markdown",
    ]
    body = run_json(argv, stdin=markdown, timeout=timeout, binary=binary)
    token = (
        _pluck(body, ("data", "document", "document_id"))
        or _pluck(body, ("data", "document_id"))
        or _pluck(body, ("document_id",))
    )
    if not token:
        raise LarkCLIError(-1, "docs +create response missing document_id",
                           argv, stdout=json.dumps(body)[:500])
    url = (
        _pluck(body, ("data", "document", "url"))
        or _pluck(body, ("data", "url"))
        or _pluck(body, ("url",))
    )

    # 2) 落进目标文件夹（drive +move 是写真实生效的 API）
    if parent_token:
        try:
            drive_move(file_token=token, target_folder_token=parent_token,
                       type_="docx", binary=binary, timeout=timeout)
        except LarkCLIError:
            pass  # 文件夹移动失败不致命，doc 已建好
    # 3) 设置标题（用 update overwrite + --new-title；标题写不上不致命）
    if title:
        try:
            docs_update_overwrite(doc_token=token, markdown=markdown,
                                  title=title, binary=binary, timeout=timeout)
        except LarkCLIError:
            pass
    return DocInfo(doc_token=token, url=url)


def drive_move(
    *,
    file_token: str,
    target_folder_token: str,
    type_: str,
    as_user: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    binary: Optional[str] = None,
) -> None:
    """``drive +move --file-token --folder-token --type``。

    ``type_`` 取值见 lark-cli ``+move --help``：file / docx / bitable / sheet / folder ...
    """
    argv = ["drive", "+move",
            "--file-token", file_token,
            "--folder-token", target_folder_token,
            "--type", type_]
    if as_user:
        argv += ["--as", "user"]
    run_json(argv, timeout=timeout, binary=binary)


def docs_update_overwrite(
    *,
    doc_token: str,
    markdown: str,
    title: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    binary: Optional[str] = None,
) -> None:
    """``docs +update --mode overwrite --markdown - [--new-title]``（v1 形式）。

    lark-cli 1.0.28 实测：v1 走 MCP update-doc，支持 ``--mode`` + ``--new-title``；
    v2 要 ``--command``，参数面有出入。这里固定 v1，更稳。
    """
    argv = [
        "docs", "+update",
        "--doc", doc_token,
        "--mode", "overwrite",
        "--markdown", "-",
    ]
    if title:
        argv += ["--new-title", title]
    run_json(argv, stdin=markdown, timeout=timeout, binary=binary)


def drive_list_folder(
    *,
    folder_token: str,
    page_size: int = 200,
    as_user: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    binary: Optional[str] = None,
) -> List[FolderEntry]:
    """``drive files list --as user --params '{folder_token,page_size}'``。"""
    params = json.dumps({"folder_token": folder_token, "page_size": page_size})
    argv = ["drive", "files", "list", "--params", params]
    if as_user:
        argv += ["--as", "user"]
    body = run_json(argv, timeout=timeout, binary=binary)
    files = _pluck(body, ("data", "files")) or _pluck(body, ("files",)) or []
    out: List[FolderEntry] = []
    if isinstance(files, list):
        for f in files:
            if not isinstance(f, dict):
                continue
            token = f.get("token") or f.get("file_token")
            name = f.get("name")
            ftype = f.get("type") or ""
            if token and name is not None:
                out.append(FolderEntry(name=name, token=token, type=ftype))
    return out


def drive_create_folder(
    *,
    parent_token: str,
    name: str,
    as_user: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    binary: Optional[str] = None,
) -> str:
    """``drive +create-folder --parent-token --name``，返回新文件夹 token。"""
    argv = ["drive", "+create-folder", "--folder-token", parent_token, "--name", name]
    if as_user:
        argv += ["--as", "user"]
    body = run_json(argv, timeout=timeout, binary=binary)
    token = (
        _pluck(body, ("data", "token"))
        or _pluck(body, ("data", "folder_token"))
        or _pluck(body, ("token",))
    )
    if not token:
        raise LarkCLIError(-1, "create-folder response missing token", argv,
                           stdout=json.dumps(body)[:500])
    return token


# ---- 组合便利函数 ---------------------------------------------------------

def find_or_create_folder(
    *,
    parent_token: str,
    name: str,
    binary: Optional[str] = None,
) -> str:
    """先 list 找同名 folder；找到则复用，否则创建。"""
    for entry in drive_list_folder(folder_token=parent_token, binary=binary):
        if entry.name == name and entry.type == "folder":
            return entry.token
    return drive_create_folder(parent_token=parent_token, name=name, binary=binary)


def find_doc_in_folder(
    *,
    folder_token: str,
    title: str,
    binary: Optional[str] = None,
) -> Optional[str]:
    """精确匹配 ``name == title AND type == "docx"``，命中返回 doc_token。"""
    for entry in drive_list_folder(folder_token=folder_token, binary=binary):
        if entry.name == title and entry.type == "docx":
            return entry.token
    return None


# ---- helpers ---------------------------------------------------------------

def _pluck(obj: Any, path: Sequence[str]) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur
