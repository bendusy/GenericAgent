"""record_writer: base 字段读写 + 应用层乐观锁（marker 字段路径）。

设计：docs/superpowers/specs/2026-05-15-m4c-base-intent-router-design.md §3, §5
飞书 base API 实测：record-upsert 无 atomic CAS，append 需 read-merge-write；
回包无 revision，所以乐观锁走 _last_writer_marker 字段路径。
"""
from __future__ import annotations

import re
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional, Tuple

from feishu_hub.lark_cli import base_record_get, base_record_upsert

# 飞书云文档 URL 正则：docx / sheets / base / wiki / minutes
_DOC_URL_RE = re.compile(
    r"https?://[\w.-]+/(?:docx|sheets|base|wiki|minutes)/[\w-]+"
)

RunState = Literal["idle", "running", "done", "aborted", "failed"]
_VALID_STATES = {"idle", "running", "done", "aborted", "failed"}

PRODUCT_FIELD = "产物"
STATE_FIELD = "运行状态"
MARKER_FIELD = "_last_writer_marker"


def set_run_state(*, record_id: str, state: str,
                  base_token: str, table_id: str) -> None:
    if state not in _VALID_STATES:
        raise ValueError(f"invalid state: {state}")
    base_record_upsert(base_token=base_token, table_id=table_id,
                       record_id=record_id, fields={STATE_FIELD: state})


def append_product(*, record_id: str, text: str,
                   base_token: str, table_id: str) -> None:
    """读-合并-写 append text 到「产物」字段（飞书 base 无 atomic append）。"""
    rec = base_record_get(base_token=base_token, table_id=table_id,
                          record_id=record_id)
    old = rec.get(PRODUCT_FIELD) or ""
    if isinstance(old, list):
        old = old[0] if old else ""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sep = f"\n\n--- {ts} ---\n"
    new = (old + sep + text) if old else f"--- {ts} ---\n{text}"
    base_record_upsert(base_token=base_token, table_id=table_id,
                       record_id=record_id, fields={PRODUCT_FIELD: new})


def _make_marker() -> str:
    host = socket.gethostname()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"{host}|{ts}|{uuid.uuid4().hex[:8]}"


def cas_acquire_running(*, record_id: str, base_token: str, table_id: str,
                        propagation_grace_s: float = 0.5) -> Tuple[Optional[str], str]:
    """应用层乐观锁：读→检查 idle→写 running+marker→等→re-get 校验 marker 是我的。

    Returns: (marker, status)。status ∈ {"ok", "non_idle", "concurrent_conflict"}.
    """
    r0 = base_record_get(base_token=base_token, table_id=table_id, record_id=record_id)
    state_list = r0.get(STATE_FIELD) or []
    state = state_list[0] if isinstance(state_list, list) and state_list else state_list
    if state != "idle":
        return None, "non_idle"
    marker = _make_marker()
    base_record_upsert(base_token=base_token, table_id=table_id, record_id=record_id,
                       fields={STATE_FIELD: "running", MARKER_FIELD: marker})
    time.sleep(propagation_grace_s)
    r1 = base_record_get(base_token=base_token, table_id=table_id, record_id=record_id)
    if r1.get(MARKER_FIELD) != marker:
        return None, "concurrent_conflict"
    return marker, "ok"


def mirror_doc_urls(*, record_id: str, target_field: str, stdout: str,
                     base_token: str, table_id: str) -> int:
    """从 stdout 抽飞书 doc URL 写到 target_field。

    Returns 写入的 URL 数量；0 表示无 URL（不调 upsert）。
    覆盖式写入（不 merge 旧值）；多 URL 用 \\n 拼接。
    """
    urls = _DOC_URL_RE.findall(stdout or "")
    if not urls:
        return 0
    seen = set()
    uniq = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    base_record_upsert(base_token=base_token, table_id=table_id,
                       record_id=record_id, fields={target_field: "\n".join(uniq)})
    return len(uniq)
