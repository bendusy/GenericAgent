"""nl_router：硬规则 NL → role+title 解析单测。"""
from pathlib import Path

import pytest

from feishu_hub.base_config import BaseConfig
from feishu_hub.nl_router import NLParseResult, _score_role, parse


def _cfg_gzh() -> BaseConfig:
    return BaseConfig(
        role="公众号-2026",
        base_token="K6Y",
        table_id="tbl_gzh",
        stage_to_bot={"📋 选题": "selector_bot", "✏️ 草稿": "drafter_bot"},
        initial_stage="📋 选题",
        nl_keywords={"strong": ["公众号", "写一篇", "文章"], "weak": ["内容", "发布"]},
    )


def test_score_role_strong_hits_score_2_each() -> None:
    cfg = _cfg_gzh()
    score, strong, weak = _score_role("公众号写一篇 AI 设计", cfg)
    assert score == 4  # 公众号 + 写一篇 = 2 + 2
    assert strong == ("公众号", "写一篇")
    assert weak == ()


def test_score_role_weak_only_score_1_each() -> None:
    cfg = _cfg_gzh()
    score, strong, weak = _score_role("一些内容关于发布", cfg)
    assert score == 2  # 内容 + 发布 = 1 + 1
    assert strong == ()
    assert weak == ("内容", "发布")


def test_score_role_mixed() -> None:
    cfg = _cfg_gzh()
    score, strong, weak = _score_role("公众号要发布的内容", cfg)
    assert score == 4  # 公众号(2) + 发布(1) + 内容(1)
    assert strong == ("公众号",)
    assert weak == ("发布", "内容")


def test_score_role_no_hit_returns_zero() -> None:
    cfg = _cfg_gzh()
    score, strong, weak = _score_role("Coder 修 bug", cfg)
    assert score == 0
    assert strong == ()
    assert weak == ()


def test_score_role_skips_when_nl_keywords_none() -> None:
    cfg = BaseConfig(
        role="X", base_token="b", table_id="t",
        stage_to_bot={"s1": "bot_a"},
        nl_keywords=None,
    )
    assert _score_role("公众号写一篇", cfg) == (0, (), ())
