"""record_writer: base 字段读写 + 应用层乐观锁（marker 字段路径）。

设计：docs/superpowers/specs/2026-05-15-m4c-base-intent-router-design.md §3, §5
飞书 base API 实测：record-upsert 无 atomic CAS，append 需 read-merge-write；
回包无 revision，所以乐观锁走 _last_writer_marker 字段路径。
"""
from __future__ import annotations

from typing import Literal

from feishu_hub.lark_cli import base_record_upsert

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
