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
    assert parse("", [_cfg_gzh()]) is None
    assert parse("   ", [_cfg_gzh()]) is None


def test_parse_returns_none_when_no_candidates_have_nl_keywords(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"X","title":"X","confidence":0.9}')
    cfg_no_kw = BaseConfig(
        role="X", base_token="b", table_id="t",
        stage_to_bot={"s1": "bot_a"}, nl_keywords=None,
    )
    assert parse("公众号写一篇 AI", [cfg_no_kw]) is None


def test_parse_returns_none_when_llm_unavailable(monkeypatch) -> None:
    _mock_llm_unavailable(monkeypatch)
    assert parse("公众号写一篇 AI", [_cfg_gzh()]) is None


def test_parse_returns_none_when_llm_raises(monkeypatch) -> None:
    _mock_llm_raises(monkeypatch, RuntimeError("network down"))
    assert parse("公众号写一篇 AI", [_cfg_gzh()]) is None


def test_parse_returns_none_when_role_is_null(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":null,"title":"","confidence":0.0,"why":"否定句"}')
    assert parse("我不想关注公众号", [_cfg_gzh()]) is None


def test_parse_returns_none_when_llm_hallucinates_role(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"小红书","title":"X","confidence":0.9}')
    assert parse("写一篇极简生活", [_cfg_gzh()]) is None


def test_parse_returns_none_when_json_malformed(monkeypatch) -> None:
    _mock_llm(monkeypatch, "Sure, here it is: not-a-json")
    assert parse("公众号写一篇", [_cfg_gzh()]) is None


def test_parse_high_confidence_routing(monkeypatch) -> None:
    _mock_llm(
        monkeypatch,
        '{"role":"公众号-2026","title":"AI 产品设计入门","confidence":0.9,"why":"明确指向公众号写作"}',
    )
    res = parse("公众号写一篇 AI 产品设计入门", [_cfg_gzh(), _cfg_coder()])
    assert res is not None
    assert res.role == "公众号-2026"
    assert res.title == "AI 产品设计入门"
    assert res.initial_stage == "📋 选题"
    assert res.confidence == pytest.approx(0.9)
    assert res.raw_text == "公众号写一篇 AI 产品设计入门"
    assert "公众号" in res.why


def test_parse_clamps_confidence_to_unit_range(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"公众号-2026","title":"X","confidence":1.5}')
    res = parse("公众号写一篇 X", [_cfg_gzh()])
    assert res is not None
    assert res.confidence == 1.0

    _mock_llm(monkeypatch, '{"role":"公众号-2026","title":"Y","confidence":-0.5}')
    res = parse("公众号写一篇 Y", [_cfg_gzh()])
    assert res is not None
    assert res.confidence == 0.0


def test_parse_handles_non_numeric_confidence(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"公众号-2026","title":"X","confidence":"high"}')
    res = parse("公众号 X", [_cfg_gzh()])
    assert res is not None
    assert res.confidence == 0.5  # fallback


def test_parse_empty_title_falls_back_to_raw_text(monkeypatch) -> None:
    _mock_llm(monkeypatch, '{"role":"公众号-2026","title":"","confidence":0.7}')
    res = parse("公众号 写一篇", [_cfg_gzh()])
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
    res = parse("alpha foo", [cfg])
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
    assert parse("alpha foo", [cfg]) is None


def test_parse_extracts_json_when_wrapped_in_markdown(monkeypatch) -> None:
    _mock_llm(
        monkeypatch,
        "```json\n{\"role\":\"公众号-2026\",\"title\":\"X\",\"confidence\":0.9}\n```",
    )
    res = parse("公众号写一篇 X", [_cfg_gzh()])
    assert res is not None
    assert res.role == "公众号-2026"
