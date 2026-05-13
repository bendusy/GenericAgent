"""tests for feishu_hub.base_intent_router."""
from __future__ import annotations

import json

import pytest

from feishu_hub.base_config import BaseConfig
from feishu_hub import base_intent_router as bir


def _configs():
    return [BaseConfig(role="公众号-2026", base_token="K6abc", table_id="tblXYZ",
                       stage_to_bot={"📋 选题": "selector_bot"})]


# ---- Cycle 4.1: _parse_base_ref ----

def test_parse_url_form():
    text = "请处理 https://feishu.cn/base/K6abc?table=tblXYZ&record=recABC 谢谢"
    assert bir._parse_base_ref(text, _configs()) == ("K6abc", "tblXYZ", "recABC")


def test_parse_short_ref():
    text = "公众号-2026 record:recABC"
    assert bir._parse_base_ref(text, _configs()) == ("K6abc", "tblXYZ", "recABC")


def test_parse_short_ref_unknown_role_returns_none():
    text = "未知角色 record:recABC"
    assert bir._parse_base_ref(text, _configs()) is None


def test_parse_garbage_returns_none():
    assert bir._parse_base_ref("hello world", _configs()) is None


def test_parse_url_with_extra_params_still_works():
    text = "https://feishu.cn/base/K6abc?table=tblXYZ&foo=bar&record=recABC"
    assert bir._parse_base_ref(text, _configs()) == ("K6abc", "tblXYZ", "recABC")
