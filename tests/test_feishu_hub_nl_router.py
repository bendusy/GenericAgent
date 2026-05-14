"""nl_router LLM 版单测：mock _get_llm_caller 返回 canned JSON。"""
import pytest

from feishu_hub.base_config import BaseConfig
from feishu_hub.nl_router import parse


def _cfg_gzh() -> BaseConfig:
    return BaseConfig(
        role="公众号-2026",
        base_token="K6Y",
        table_id="tbl_gzh",
        stage_to_bot={"📋 选题": "selector_bot", "✏️ 草稿": "drafter_bot"},
        initial_stage="📋 选题",
        nl_keywords={"strong": ["公众号", "写一篇", "文章"], "weak": ["内容", "发布"]},
    )


def _cfg_coder() -> BaseConfig:
    return BaseConfig(
        role="Coder",
        base_token="TauG",
        table_id="tbl_coder",
        stage_to_bot={"🎯 待办": "planner_bot"},
        initial_stage="🎯 待办",
        nl_keywords={"strong": ["bug", "issue", "修"], "weak": []},
    )


def _mock_llm(monkeypatch, response: str) -> None:
    """Replace _get_llm_caller with a fake returning canned response."""
    def fake_caller(prompt: str) -> str:
        return response
    monkeypatch.setattr("feishu_hub.nl_router._get_llm_caller", lambda: fake_caller)


def _mock_llm_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("feishu_hub.nl_router._get_llm_caller", lambda: None)


def _mock_llm_raises(monkeypatch, exc: Exception) -> None:
    def fake_caller(prompt: str) -> str:
        raise exc
    monkeypatch.setattr("feishu_hub.nl_router._get_llm_caller", lambda: fake_caller)


def test_parse_returns_none_on_empty_text(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":null}')
    res, tried = parse("", [_cfg_gzh()])
    assert res is None and tried is False
    res, tried = parse("   ", [_cfg_gzh()])
    assert res is None and tried is False


def test_parse_returns_none_when_no_candidates_have_nl_keywords(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"X","title":"X","confidence":0.9}')
    cfg_no_kw = BaseConfig(
        role="X", base_token="b", table_id="t",
        stage_to_bot={"s1": "bot_a"}, nl_keywords=None,
    )
    res, tried = parse("公众号写一篇 AI", [cfg_no_kw])
    assert res is None and tried is False


def test_parse_returns_none_when_llm_unavailable(monkeypatch) -> None:
    _mock_llm_unavailable(monkeypatch)
    res, tried = parse("公众号写一篇 AI", [_cfg_gzh()])
    assert res is None and tried is False


def test_parse_returns_none_when_llm_raises(monkeypatch) -> None:
    _mock_llm_raises(monkeypatch, RuntimeError("network down"))
    res, tried = parse("公众号写一篇 AI", [_cfg_gzh()])
    assert res is None and tried is True


def test_parse_returns_none_when_role_is_null(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":null,"title":"","confidence":0.0,"why":"否定句"}')
    res, tried = parse("我不想关注公众号", [_cfg_gzh()])
    assert res is None and tried is True


def test_parse_returns_none_when_llm_hallucinates_role(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"小红书","title":"X","confidence":0.9}')
    res, tried = parse("写一篇极简生活", [_cfg_gzh()])
    assert res is None and tried is True


def test_parse_returns_none_when_json_malformed(monkeypatch) -> None:
    _mock_llm(monkeypatch, "Sure, here it is: not-a-json")
    res, tried = parse("公众号写一篇", [_cfg_gzh()])
    assert res is None and tried is True


def test_parse_high_confidence_routing(monkeypatch) -> None:
    _mock_llm(
        monkeypatch,
        '{"role":"公众号-2026","title":"AI 产品设计入门","confidence":0.9,"why":"明确指向公众号写作"}',
    )
    res, tried = parse("公众号写一篇 AI 产品设计入门", [_cfg_gzh(), _cfg_coder()])
    assert res is not None and tried is False
    assert res.role == "公众号-2026"
    assert res.title == "AI 产品设计入门"
    assert res.initial_stage == "📋 选题"
    assert res.confidence == pytest.approx(0.9)
    assert res.raw_text == "公众号写一篇 AI 产品设计入门"
    assert "公众号" in res.why


def test_parse_clamps_confidence_to_unit_range(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"公众号-2026","title":"X","confidence":1.5}')
    res, _ = parse("公众号写一篇 X", [_cfg_gzh()])
    assert res is not None
    assert res.confidence == 1.0

    _mock_llm(monkeypatch, '{"role":"公众号-2026","title":"Y","confidence":-0.5}')
    res, _ = parse("公众号写一篇 Y", [_cfg_gzh()])
    assert res is not None
    assert res.confidence == 0.0


def test_parse_handles_non_numeric_confidence(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"公众号-2026","title":"X","confidence":"high"}')
    res, _ = parse("公众号 X", [_cfg_gzh()])
    assert res is not None
    assert res.confidence == 0.5  # fallback


def test_parse_empty_title_falls_back_to_raw_text(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"公众号-2026","title":"","confidence":0.7}')
    res, _ = parse("公众号 写一篇", [_cfg_gzh()])
    assert res is not None
    assert res.title == "公众号 写一篇"


def test_parse_initial_stage_fallback_to_first_stage_to_bot(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"Y","title":"foo","confidence":0.9}')
    cfg = BaseConfig(
        role="Y", base_token="b", table_id="t",
        stage_to_bot={"stage_one": "bot_a", "stage_two": "bot_b"},
        nl_keywords={"strong": ["alpha"], "weak": []},
        initial_stage=None,
    )
    res, _ = parse("alpha foo", [cfg])
    assert res is not None
    assert res.initial_stage == "stage_one"


def test_parse_returns_none_when_no_initial_stage_and_empty_stage_to_bot(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"Z","title":"foo","confidence":0.9}')
    cfg = BaseConfig(
        role="Z", base_token="b", table_id="t",
        stage_to_bot={},
        nl_keywords={"strong": ["alpha"], "weak": []},
        initial_stage=None,
    )
    res, tried = parse("alpha foo", [cfg])
    assert res is None and tried is True


def test_parse_extracts_json_when_wrapped_in_markdown(monkeypatch) -> None:
    _mock_llm(
        monkeypatch,
        "```json\n{\"role\":\"公众号-2026\",\"title\":\"X\",\"confidence\":0.9}\n```",
    )
    res, _ = parse("公众号写一篇 X", [_cfg_gzh()])
    assert res is not None
    assert res.role == "公众号-2026"


# ---- Issue 7: LLM 输出格式 edge case 测试 ----

def test_parse_handles_json_with_extra_keys(monkeypatch) -> None:
    """LLM 多返回了字段 → 应该忽略不报错。"""
    _mock_llm(
        monkeypatch,
        '{"role":"公众号-2026","title":"X","confidence":0.9,"why":"foo","extra_key":"junk","another":42}',
    )
    res, tried_and_failed = parse("公众号写一篇 X", [_cfg_gzh()])
    assert res is not None
    assert res.role == "公众号-2026"
    assert tried_and_failed is False


def test_parse_handles_json_missing_optional_keys(monkeypatch) -> None:
    """LLM 漏了 why / confidence → 用默认值。"""
    _mock_llm(monkeypatch, '{"role":"公众号-2026","title":"X"}')
    res, tried_and_failed = parse("公众号 X", [_cfg_gzh()])
    assert res is not None
    assert res.confidence == 0.5  # fallback
    assert res.why == ""
    assert tried_and_failed is False


def test_parse_handles_prose_before_json(monkeypatch) -> None:
    """LLM 输出有前导话术 → 正则提取 JSON 仍 OK。"""
    _mock_llm(
        monkeypatch,
        'Sure, here is the JSON:\n\n{"role":"公众号-2026","title":"X","confidence":0.9}\n\nLet me know!',
    )
    res, tried_and_failed = parse("公众号 X", [_cfg_gzh()])
    assert res is not None
    assert res.role == "公众号-2026"
    assert tried_and_failed is False


def test_parse_returns_tried_and_failed_on_llm_exception(monkeypatch) -> None:
    """codex Q5: LLM 异常应返回 (None, True) 给 try_handle_nl 触发兜底回复。"""
    _mock_llm_raises(monkeypatch, RuntimeError("network down"))
    res, tried_and_failed = parse("公众号写一篇 AI", [_cfg_gzh()])
    assert res is None
    assert tried_and_failed is True


def test_parse_returns_tried_and_failed_on_null_role(monkeypatch) -> None:
    """LLM 显式 role=null 也算 tried_and_failed（spec §3 兜底）。"""
    _mock_llm(monkeypatch, '{"role":null,"title":"","confidence":0.0,"why":"否定"}')
    res, tried_and_failed = parse("我不想关注公众号", [_cfg_gzh()])
    assert res is None
    assert tried_and_failed is True


def test_parse_returns_silent_when_llm_unavailable(monkeypatch) -> None:
    """LLM 未配置应静默 fall-through，不触发兜底回复。"""
    _mock_llm_unavailable(monkeypatch)
    res, tried_and_failed = parse("公众号写一篇 AI", [_cfg_gzh()])
    assert res is None
    assert tried_and_failed is False


def test_parse_returns_silent_when_no_text(monkeypatch) -> None:
    """空 text → silent，不调 LLM。"""
    _mock_llm(monkeypatch, '{"role":"X"}')  # 不应被调用
    res, tried_and_failed = parse("", [_cfg_gzh()])
    assert res is None
    assert tried_and_failed is False


def test_parse_returns_silent_when_no_candidates(monkeypatch) -> None:
    """没有 nl_keywords 配置 → silent，不调 LLM。"""
    _mock_llm(monkeypatch, '{"role":"X"}')
    cfg = BaseConfig(role="X", base_token="b", table_id="t",
                     stage_to_bot={"s1": "bot_a"}, nl_keywords=None)
    res, tried_and_failed = parse("公众号写一篇 AI", [cfg])
    assert res is None
    assert tried_and_failed is False


def test_parse_invalidates_cache_after_llm_exception(monkeypatch) -> None:
    """codex Q5: LLM 调用异常后应清 _llm_caller cache，下次重新 resolve。"""
    import feishu_hub.nl_router as nr

    # 重置全局 cache
    nr._llm_caller = None

    # 第 1 次 resolve → 返回 always-raises caller
    def raising_caller(prompt: str) -> str:
        raise RuntimeError("transient network error")

    monkeypatch.setattr("feishu_hub.nl_router._get_llm_caller", lambda: raising_caller)

    # 第 1 次调用：LLM raises → (None, True)
    res1, failed1 = parse("公众号写一篇 X", [_cfg_gzh()])
    assert res1 is None
    assert failed1 is True

    # cache 应被清除：_llm_caller = None
    assert nr._llm_caller is None
