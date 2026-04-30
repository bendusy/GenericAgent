import json
import subprocess
from unittest.mock import patch, MagicMock

from frontends import lark_bridge


def _completed(returncode=0, stdout="", stderr=""):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


@patch("frontends.lark_bridge.subprocess.run")
def test_run_under_threshold_returns_full_stdout(mock_run):
    payload = json.dumps({"event": "x"})
    mock_run.return_value = _completed(0, payload, "")
    r = lark_bridge.run(["calendar", "+agenda"])
    assert r.ok is True
    assert r.stdout == payload
    assert r.head == payload
    assert r.doc_url is None
    assert r.error is None
    args = mock_run.call_args[0][0]
    assert args[0] == lark_bridge.LARK_CLI
    assert args[1:3] == ["calendar", "+agenda"]
    assert "--format" in args and "json" in args


@patch("frontends.lark_bridge.subprocess.run")
def test_run_passes_jq_when_provided(mock_run):
    mock_run.return_value = _completed(0, "{}", "")
    lark_bridge.run(["wiki", "+search"], jq=".items[0]")
    args = mock_run.call_args[0][0]
    assert "--jq" in args
    assert args[args.index("--jq") + 1] == ".items[0]"


@patch("frontends.lark_bridge.subprocess.run")
def test_run_nonzero_exit_returns_error(mock_run):
    mock_run.return_value = _completed(1, "", "boom")
    r = lark_bridge.run(["calendar", "+agenda"])
    assert r.ok is False
    assert "boom" in r.error


@patch("frontends.lark_bridge.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=60))
def test_run_timeout(mock_run):
    r = lark_bridge.run(["calendar", "+agenda"], timeout=60)
    assert r.ok is False
    assert "timeout" in r.error.lower()


@patch("frontends.lark_bridge.subprocess.run", side_effect=FileNotFoundError())
def test_run_lark_cli_missing(mock_run):
    r = lark_bridge.run(["calendar", "+agenda"])
    assert r.ok is False
    assert "not found" in r.error.lower()


@patch("frontends.lark_bridge.subprocess.run")
def test_run_overflow_uploads_doc(mock_run):
    big = "x" * (lark_bridge.OVERFLOW_BYTES + 100)
    upload_resp = json.dumps({"url": "https://example.feishu.cn/docx/abc"})
    # First call: original command. Second call: docs +create.
    mock_run.side_effect = [_completed(0, big, ""), _completed(0, upload_resp, "")]
    r = lark_bridge.run(["base", "records", "search"], title_hint="base-dump")
    assert r.ok is True
    assert r.doc_url == "https://example.feishu.cn/docx/abc"
    assert r.head.endswith("[truncated, full content in doc] ...\n")
    upload_call = mock_run.call_args_list[1][0][0]
    assert upload_call[1:3] == ["docs", "+create"]
    assert "--title" in upload_call
    assert "--markdown" in upload_call
    # markdown content is passed via stdin, so upload_call has "-" placeholder
    assert upload_call[upload_call.index("--markdown") + 1] == "-"


@patch("frontends.lark_bridge.subprocess.run")
def test_run_overflow_upload_failure_keeps_head(mock_run):
    big = "x" * (lark_bridge.OVERFLOW_BYTES + 100)
    mock_run.side_effect = [_completed(0, big, ""), _completed(1, "", "upload failed")]
    r = lark_bridge.run(["base", "records", "search"])
    assert r.ok is True
    assert r.doc_url is None
    assert "[truncated" in r.head


@patch("frontends.lark_bridge.subprocess.run")
def test_run_overflow_upload_bad_json_returns_none_url(mock_run):
    big = "x" * (lark_bridge.OVERFLOW_BYTES + 100)
    mock_run.side_effect = [_completed(0, big, ""), _completed(0, "not json", "")]
    r = lark_bridge.run(["base", "records", "search"])
    assert r.ok is True
    assert r.doc_url is None
