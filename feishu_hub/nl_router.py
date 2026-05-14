"""硬规则 NL → (role, title, initial_stage) 解析。

设计：docs/superpowers/specs/2026-05-14-m5-user-first-pivot-design.md §3
M5.A 范围：仅做硬规则；GPT-4o-mini 留 M5.B。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from feishu_hub.base_config import BaseConfig


@dataclass(frozen=True)
class NLParseResult:
    role: str
    title: str
    initial_stage: str
    confidence: float  # 0.0~1.0
    raw_text: str
    matched_strong: Tuple[str, ...]
    matched_weak: Tuple[str, ...]


def _score_role(text: str, cfg: BaseConfig) -> Tuple[int, Tuple[str, ...], Tuple[str, ...]]:
    """Score role against text: strong +2, weak +1. Returns (score, strong_hits, weak_hits)."""
    kw = cfg.nl_keywords
    if not kw:
        return 0, (), ()
    strong_hits = tuple(sorted(
        (k for k in kw.get("strong", []) if k in text),
        key=lambda k: text.index(k),
    ))
    weak_hits = tuple(sorted(
        (k for k in kw.get("weak", []) if k in text),
        key=lambda k: text.index(k),
    ))
    score = len(strong_hits) * 2 + len(weak_hits)
    return score, strong_hits, weak_hits


def parse(text: str, configs: List[BaseConfig]) -> Optional[NLParseResult]:
    """Stub for Phase 4."""
    raise NotImplementedError
