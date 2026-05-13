"""Tests for feishu_hub.lark_cli base_* helpers (Phase 0 of M4.C)."""
from __future__ import annotations

import json
from unittest.mock import patch


# --- base_record_get ------------------------------------------------------

class TestBaseRecordGet:
    @patch("feishu_hub.lark_cli.run_json")
    def test_parses_fields(self, run_json):
        run_json.return_value = {
            "code": 0,
            "data": {"record": {"fields": {"任务标题": "hi", "阶段": ["📋 选题"]}}},
        }
        from feishu_hub.lark_cli import base_record_get

        rec = base_record_get(base_token="bt", table_id="tbl", record_id="rec1")
        assert rec == {"任务标题": "hi", "阶段": ["📋 选题"]}
        argv = run_json.call_args.args[0]
        assert argv[:2] == ["base", "+record-get"]
        assert "--base-token" in argv and argv[argv.index("--base-token") + 1] == "bt"
        assert "--table-id" in argv and argv[argv.index("--table-id") + 1] == "tbl"
        assert "--record-id" in argv and argv[argv.index("--record-id") + 1] == "rec1"
        assert "--format" in argv and argv[argv.index("--format") + 1] == "json"


# --- base_record_upsert ---------------------------------------------------

class TestBaseRecordUpsertCreate:
    @patch("feishu_hub.lark_cli.run_json")
    def test_create_returns_extracted_id(self, run_json):
        run_json.return_value = {
            "code": 0,
            "data": {"record": {"record_id_list": ["recNEW"]}},
        }
        from feishu_hub.lark_cli import base_record_upsert

        rid = base_record_upsert(
            base_token="bt", table_id="tbl",
            fields={"任务标题": "x", "阶段": ["📋 选题"]},
        )
        assert rid == "recNEW"
        argv = run_json.call_args.args[0]
        assert "--record-id" not in argv  # create path

    @patch("feishu_hub.lark_cli.run_json")
    def test_create_payload_contains_field_map(self, run_json):
        run_json.return_value = {"data": {"record": {"record_id_list": ["recX"]}}}
        from feishu_hub.lark_cli import base_record_upsert

        base_record_upsert(
            base_token="bt", table_id="tbl",
            fields={"任务标题": "hello", "阶段": ["📋 选题"]},
        )
        argv = run_json.call_args.args[0]
        assert "--json" in argv
        payload = json.loads(argv[argv.index("--json") + 1])
        assert payload == {"任务标题": "hello", "阶段": ["📋 选题"]}


class TestBaseRecordUpsertUpdate:
    @patch("feishu_hub.lark_cli.run_json")
    def test_update_passes_through_record_id(self, run_json):
        run_json.return_value = {"code": 0, "data": {}}
        from feishu_hub.lark_cli import base_record_upsert

        rid = base_record_upsert(
            base_token="bt", table_id="tbl",
            fields={"任务标题": "x"},
            record_id="recOLD",
        )
        assert rid == "recOLD"
        argv = run_json.call_args.args[0]
        assert "--record-id" in argv
        assert argv[argv.index("--record-id") + 1] == "recOLD"


# --- base_record_search ---------------------------------------------------

class TestBaseRecordSearch:
    @patch("feishu_hub.lark_cli.run_json")
    def test_returns_items_list(self, run_json):
        run_json.return_value = {
            "code": 0,
            "data": {"items": [{"record_id": "r1"}, {"record_id": "r2"}]},
        }
        from feishu_hub.lark_cli import base_record_search

        items = base_record_search(
            base_token="bt", table_id="tbl", filter_expr="hello",
        )
        assert len(items) == 2
        assert items[0]["record_id"] == "r1"

    @patch("feishu_hub.lark_cli.run_json")
    def test_filter_propagates_into_json(self, run_json):
        run_json.return_value = {"data": {"items": []}}
        from feishu_hub.lark_cli import base_record_search

        base_record_search(
            base_token="bt", table_id="tbl",
            filter_expr="alpha", page_size=50,
        )
        argv = run_json.call_args.args[0]
        assert "+record-search" in argv
        assert "--json" in argv
        payload = json.loads(argv[argv.index("--json") + 1])
        assert payload.get("keyword") == "alpha"
        assert payload.get("limit") == 50


# --- base_record_delete ---------------------------------------------------

class TestBaseRecordDelete:
    @patch("feishu_hub.lark_cli.run_json")
    def test_argv_contains_yes_and_record_id(self, run_json):
        run_json.return_value = {"code": 0, "data": {}}
        from feishu_hub.lark_cli import base_record_delete

        result = base_record_delete(
            base_token="bt", table_id="tbl", record_id="recDEL",
        )
        assert result is None
        argv = run_json.call_args.args[0]
        assert "+record-delete" in argv
        assert "--yes" in argv
        assert "--record-id" in argv
        assert argv[argv.index("--record-id") + 1] == "recDEL"
