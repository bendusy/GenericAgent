"""硬规则 NL → (role, title, initial_stage) 解析。

设计：docs/superpowers/specs/2026-05-14-m5-user-first-pivot-design.md §3
M5.A 范围：仅做硬规则；GPT-4o-mini 留 M5.B。
"""
from __future__ import annotations

import re
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


# 首尾常见助词/语气词/代词，剥掉避免"帮我 写一篇 → 帮我 X"留尾巴
_PARTICLES = ("帮我", "请", "麻烦", "想", "能不能", "可以", "给我", "替我", "的", "吧", "呢", "啊", "哦")


def _extract_title(text: str, matched_keywords: Tuple[str, ...]) -> str:
    """Strip matched keywords + leading/trailing particles. Fallback to full text if result is empty / <2 chars."""
    work = text
    for kw in matched_keywords:
        work = work.replace(kw, " ")
    # collapse multiple spaces
    work = re.sub(r"\s+", " ", work).strip()
    # strip leading/trailing particles iteratively
    changed = True
    while changed:
        changed = False
        for p in _PARTICLES:
            if work.startswith(p):
                work = work[len(p):].strip()
                changed = True
                break
            if work.endswith(p):
                work = work[:-len(p)].strip()
                changed = True
                break
    if len(work) < 2:
        return text.strip()
    return work


def parse(text: str, configs: List[BaseConfig]) -> Optional[NLParseResult]:
    """Hard-rule NL parser.

    返回 None 的情况：
    - text 为空
    - 所有 role 都 score==0
    - 最高分有并列（不自动选）
    - 选出的 role 既无 initial_stage 也无 stage_to_bot fallback
    """
    if not text or not text.strip():
        return None

    scored: List[Tuple[BaseConfig, int, Tuple[str, ...], Tuple[str, ...]]] = []
    for cfg in configs:
        score, strong, weak = _score_role(text, cfg)
        if score > 0:
            scored.append((cfg, score, strong, weak))

    if not scored:
        return None

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[0]
    # 并列：最高分有 >1 项 → 拒绝（追问由外层处理；plan §3 fallback "完全识别不了"）
    if len(scored) > 1 and scored[1][1] == top[1]:
        return None

    cfg, score, strong, weak = top

    initial_stage = cfg.initial_stage
    if initial_stage is None:
        if cfg.stage_to_bot:
            initial_stage = next(iter(cfg.stage_to_bot.keys()))
        else:
            return None  # 该 role 不能参与 NL 建行

    title = _extract_title(text, strong + weak)

    # confidence: 强关键词命中 → 0.85；只有弱关键词 → 0.5
    confidence = 0.85 if strong else 0.5

    return NLParseResult(
        role=cfg.role,
        title=title,
        initial_stage=initial_stage,
        confidence=confidence,
        raw_text=text,
        matched_strong=strong,
        matched_weak=weak,
    )
