from unittest.mock import patch
import pytest
from feishu_hub.record_writer import set_run_state


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
