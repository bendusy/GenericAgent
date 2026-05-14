"""nl_router：硬规则 NL → role+title 解析单测。"""
from feishu_hub.base_config import BaseConfig
from feishu_hub.nl_router import _extract_title, _score_role, parse


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


def test_extract_title_strips_matched_keywords() -> None:
    title = _extract_title(
        text="公众号写一篇 AI 产品设计入门",
        matched_keywords=("公众号", "写一篇"),
    )
    assert title == "AI 产品设计入门"


def test_extract_title_strips_leading_particles() -> None:
    title = _extract_title(
        text="公众号 帮我 写一篇 AI 产品设计",
        matched_keywords=("公众号", "写一篇"),
    )
    # "帮我" 是首尾助词，应剥掉
    assert title == "AI 产品设计"


def test_extract_title_fallback_to_full_when_too_short() -> None:
    title = _extract_title(
        text="公众号写一篇",
        matched_keywords=("公众号", "写一篇"),
    )
    # 剥完只剩空字符串 → fallback 整句
    assert title == "公众号写一篇"


def test_extract_title_trims_whitespace() -> None:
    title = _extract_title(
        text="  公众号   写一篇   极简生活   ",
        matched_keywords=("公众号", "写一篇"),
    )
    assert title == "极简生活"


def _cfg_coder() -> BaseConfig:
    return BaseConfig(
        role="Coder",
        base_token="TauG",
        table_id="tbl_coder",
        stage_to_bot={"🎯 待办": "planner_bot"},
        initial_stage="🎯 待办",
        nl_keywords={"strong": ["bug", "issue", "修"], "weak": ["代码", "调试"]},
    )


def test_parse_picks_highest_score_role() -> None:
    res = parse("公众号写一篇 AI 产品设计", [_cfg_gzh(), _cfg_coder()])
    assert res is not None
    assert res.role == "公众号-2026"
    assert res.title == "AI 产品设计"
    assert res.initial_stage == "📋 选题"
    assert res.confidence >= 0.7
    assert res.raw_text == "公众号写一篇 AI 产品设计"


def test_parse_returns_none_when_no_role_scored() -> None:
    res = parse("天气真好", [_cfg_gzh(), _cfg_coder()])
    assert res is None


def test_parse_low_confidence_when_only_weak_hits() -> None:
    res = parse("发布一些内容", [_cfg_gzh(), _cfg_coder()])
    # 公众号-2026 弱 hit 内容+发布 score=2, 但全弱 → confidence < 0.7
    assert res is not None
    assert res.role == "公众号-2026"
    assert res.confidence < 0.7


def test_parse_tie_returns_none() -> None:
    # 同分（公众号-2026 strong=公众号; Coder strong=bug）造成 4=4 平局
    cfg_gzh_eq = BaseConfig(
        role="公众号-2026", base_token="K6Y", table_id="tbl_gzh",
        stage_to_bot={"📋 选题": "selector_bot"}, initial_stage="📋 选题",
        nl_keywords={"strong": ["公众号", "写一篇"], "weak": []},
    )
    cfg_coder_eq = BaseConfig(
        role="Coder", base_token="TauG", table_id="tbl_coder",
        stage_to_bot={"🎯 待办": "planner_bot"}, initial_stage="🎯 待办",
        nl_keywords={"strong": ["bug", "issue"], "weak": []},
    )
    res = parse("公众号写一篇 bug issue", [cfg_gzh_eq, cfg_coder_eq])
    assert res is None  # 4 == 4 tie → reject


def test_parse_skips_role_without_nl_keywords() -> None:
    cfg_unconfigured = BaseConfig(
        role="X", base_token="b", table_id="t",
        stage_to_bot={"s1": "bot_a"},
        nl_keywords=None,
    )
    res = parse("公众号写一篇 AI", [_cfg_gzh(), cfg_unconfigured])
    assert res is not None
    assert res.role == "公众号-2026"


def test_parse_initial_stage_fallback_to_first_stage_to_bot() -> None:
    cfg_no_initial = BaseConfig(
        role="Y", base_token="b", table_id="t",
        stage_to_bot={"stage_one": "bot_a", "stage_two": "bot_b"},
        nl_keywords={"strong": ["alpha"], "weak": []},
        initial_stage=None,
    )
    res = parse("alpha foo", [cfg_no_initial])
    assert res is not None
    assert res.initial_stage == "stage_one"


def test_parse_initial_stage_none_when_stage_to_bot_empty() -> None:
    cfg_empty_stage = BaseConfig(
        role="Z", base_token="b", table_id="t",
        stage_to_bot={},
        nl_keywords={"strong": ["alpha"], "weak": []},
    )
    res = parse("alpha foo", [cfg_empty_stage])
    # 无 initial_stage 又无 stage_to_bot → 不能参与 NL 建行
    assert res is None
