from unittest.mock import patch
import pytest
from feishu_hub.record_writer import append_product, set_run_state


def test_set_run_state_writes_correct_payload():
    captured = {}

    def fake_upsert(*, base_token, table_id, record_id, fields, **kw):
        captured.update(fields=fields, record_id=record_id,
                        base_token=base_token, table_id=table_id)
        return record_id

    with patch("feishu_hub.record_writer.base_record_upsert", side_effect=fake_upsert):
        set_run_state(record_id="rec1", state="running",
                      base_token="bt", table_id="tbl")
    assert captured["fields"] == {"运行状态": "running"}
    assert captured["record_id"] == "rec1"


def test_set_run_state_rejects_unknown_state():
    with pytest.raises(ValueError, match="invalid state"):
        set_run_state(record_id="rec1", state="bogus",
                      base_token="bt", table_id="tbl")


def _patch_get_upsert(get_return, capture):
    def fake_get(*, base_token, table_id, record_id, **kw):
        return get_return
    def fake_upsert(*, base_token, table_id, record_id, fields, **kw):
        capture.update(fields=fields, record_id=record_id)
        return record_id
    return (
        patch("feishu_hub.record_writer.base_record_get", side_effect=fake_get),
        patch("feishu_hub.record_writer.base_record_upsert", side_effect=fake_upsert),
    )


def test_append_product_first_write():
    captured = {}
    g, u = _patch_get_upsert({}, captured)
    with g, u:
        append_product(record_id="rec1", text="hello",
                       base_token="bt", table_id="tbl")
    new = captured["fields"]["产物"]
    assert "hello" in new
    assert "---" in new


def test_append_product_preserves_old_content():
    captured = {}
    g, u = _patch_get_upsert({"产物": "old chunk"}, captured)
    with g, u:
        append_product(record_id="rec1", text="new chunk",
                       base_token="bt", table_id="tbl")
    new = captured["fields"]["产物"]
    assert "old chunk" in new
    assert "new chunk" in new
    assert new.index("old chunk") < new.index("new chunk")


def test_append_product_handles_old_as_list_form():
    captured = {}
    g, u = _patch_get_upsert({"产物": ["legacy"]}, captured)
    with g, u:
        append_product(record_id="rec1", text="fresh",
                       base_token="bt", table_id="tbl")
    new = captured["fields"]["产物"]
    assert "legacy" in new
    assert "fresh" in new
