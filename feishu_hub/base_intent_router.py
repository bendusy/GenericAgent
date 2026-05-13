"""IM 消息 → base record 触发路由。

设计：docs/superpowers/specs/2026-05-15-m4c-base-intent-router-design.md §1, §2
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from feishu_hub.base_config import BaseConfig, resolve_by_role

_URL_RE = re.compile(
    r"https?://[\w.-]+/base/(\w+)\?[^\s]*?table=(tbl\w+)[^\s]*?record=(rec\w+)"
)
_SHORT_RE = re.compile(r"(\S[^\s]*?)\s+record:(rec\w+)")
_RUN_RE = re.compile(r"/run\s+(.+?)\s*$", re.MULTILINE | re.DOTALL)


def _parse_base_ref(text: str, configs: List[BaseConfig]) -> Optional[Tuple[str, str, str]]:
    """Returns (base_token, table_id, record_id) or None."""
    m = _URL_RE.search(text)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = _SHORT_RE.search(text.strip())
    if m:
        role, record_id = m.group(1).strip(), m.group(2)
        cfg = resolve_by_role(configs, role)
        if cfg:
            return cfg.base_token, cfg.table_id, record_id
    return None
