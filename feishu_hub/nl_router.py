"""LLM 版 NL → (role, title, initial_stage) 解析。

设计：docs/superpowers/specs/2026-05-14-m5-user-first-pivot-design.md §3
M5.A 升级版：1 次 llmcore 调用替代硬规则（spec §3 原 M5.B 计划前移）。
LLM 失败 / 无 GA → parse() 返回 None（静默 fall-through，不阻塞主路径）。
复用 feishu_hub.llm_summary.make_ga_summarizer（GA llmcore wrapper）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from feishu_hub.base_config import BaseConfig

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NLParseResult:
    role: str
    title: str
    initial_stage: str
    confidence: float
    raw_text: str
    why: str = ""


_PROMPT_TEMPLATE = """你是飞书机器人意图分类器。根据用户消息选择匹配的 role。

可选 role：
{roles}

用户消息：「{text}」

规则：
- 否定句（"不要..."/"别..."/"没想..."）一律返回 role=null
- 闲聊（如"天气真好"）返回 role=null
- 命中 role 时提取简洁标题：去掉口语化前缀（"帮我"/"请"/"想"等），保留核心动作
- confidence：强匹配 0.85+，模糊 0.5-0.7，不确定 <0.5

只返回 JSON（无 markdown 包裹，无多余文字）：
{{"role": "<role 名或 null>", "title": "<标题>", "confidence": <0-1 数字>, "why": "<一句中文理由>"}}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Module-level cache：惰性 resolve LLM caller (prompt → str)。
# 显式声明类型让 monkeypatch 测试可替换。
_llm_caller: Optional[Callable[[str], str]] = None


def _get_llm_caller() -> Optional[Callable[[str], str]]:
    """复用 GA llmcore 客户端；GA 不可用返回 None。"""
    global _llm_caller
    if _llm_caller is not None:
        return _llm_caller
    try:
        from feishu_hub.llm_summary import make_ga_summarizer
    except Exception:
        _log.debug("nl_router: llm_summary import failed", exc_info=True)
        return None
    _llm_caller = make_ga_summarizer()
    return _llm_caller


def _build_prompt(text: str, candidates: List[BaseConfig]) -> str:
    role_lines: List[str] = []
    for cfg in candidates:
        kw = cfg.nl_keywords or {}
        strong = kw.get("strong", [])
        hint = f"（提示词：{', '.join(strong)}）" if strong else ""
        role_lines.append(f"- {cfg.role}{hint}")
    return _PROMPT_TEMPLATE.format(roles="\n".join(role_lines), text=text.strip())


def _parse_llm_json(raw: str) -> Optional[dict]:
    if not raw or not raw.strip():
        return None
    m = _JSON_RE.search(raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse(text: str, configs: List[BaseConfig]) -> Optional[NLParseResult]:
    """LLM-based NL parser.

    返回 None 的情况：
    - text 为空
    - 没有 candidate role（配置无 nl_keywords）
    - LLM 不可用 / 抛异常 / 返回非 JSON
    - LLM 返回 role=null（否定 / 闲聊）
    - LLM 幻觉了一个不在 candidates 里的 role
    - 选出的 role 既无 initial_stage 也无 stage_to_bot fallback
    """
    if not text or not text.strip():
        return None

    candidates = [c for c in configs if c.nl_keywords]
    if not candidates:
        return None

    caller = _get_llm_caller()
    if caller is None:
        return None

    try:
        raw = caller(_build_prompt(text, candidates))
    except Exception:
        _log.exception("nl_router LLM call failed: text=%r", text[:100])
        return None

    data = _parse_llm_json(raw)
    if not data:
        return None

    role = data.get("role")
    if not isinstance(role, str) or not role.strip():
        return None

    cfg = next((c for c in candidates if c.role == role), None)
    if cfg is None:
        _log.warning("nl_router: LLM hallucinated role %r not in candidates", role)
        return None

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    raw_title = data.get("title", "")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if not title:
        title = text.strip()

    initial_stage = cfg.initial_stage
    if initial_stage is None:
        if cfg.stage_to_bot:
            initial_stage = next(iter(cfg.stage_to_bot.keys()))
        else:
            return None

    raw_why = data.get("why", "")
    why = raw_why if isinstance(raw_why, str) else ""

    return NLParseResult(
        role=cfg.role,
        title=title,
        initial_stage=initial_stage,
        confidence=confidence,
        raw_text=text,
        why=why,
    )
