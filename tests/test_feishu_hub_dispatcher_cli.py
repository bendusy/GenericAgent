"""feishu_hub.dispatcher.cli 单测：fire / test-rule / replay 入口。

tail 模式因常驻不便单测，只覆盖 build_dctx / parser。
"""
import datetime as _dt
import json
from pathlib import Path
from unittest.mock import patch

import pytest

yaml = pytest.importorskip("yaml")

from feishu_hub import config as cfgmod
from feishu_hub.dispatcher import cli, loop, runners


@pytest.fixture
def fhub_home(monkeypatch, tmp_path):
    home = tmp_path / "fhub"
    monkeypatch.setenv(cfgmod.ENV_ROOT, str(home))
    return home


@pytest.fixture
def rules_yaml(fhub_home):
    fhub_home.mkdir(parents=True, exist_ok=True)
    p = fhub_home / "rules.yaml"
    p.write_text(
        "version: 1\n"
        "rules:\n"
        "  - name: demo\n"
        "    when:\n"
        "      event_type: agent.session_end\n"
        "    action:\n"
        "      runner: noop\n"
        "      prompt: 'echo {{trigger.summary}}'\n",
        encoding="utf-8",
    )
    return p


# ---- fire -----------------------------------------------------------------

def test_fire_from_stdin(monkeypatch, rules_yaml, fhub_home, capsys):
    event = {"event_type": "agent.session_end", "event_id": "E1",
             "summary": "hello world"}
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(event)))
    rc = cli.main(["fire"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dispatched 1" in out


def test_fire_no_input_returns_2(monkeypatch, rules_yaml, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin(""))
    rc = cli.main(["fire"])
    assert rc == 2


def test_fire_invalid_json_returns_2(monkeypatch, rules_yaml, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin("not json"))
    rc = cli.main(["fire"])
    assert rc == 2


def test_fire_non_object_returns_2(monkeypatch, rules_yaml, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps([1, 2])))
    rc = cli.main(["fire"])
    assert rc == 2


def test_fire_event_file_path(rules_yaml, fhub_home, tmp_path, capsys):
    ev = {"event_type": "agent.session_end", "event_id": "E2", "summary": "x"}
    p = tmp_path / "evt.json"
    p.write_text(json.dumps(ev), encoding="utf-8")
    rc = cli.main(["fire", "--event-file", str(p)])
    assert rc == 0


def test_fire_no_rule_hit_returns_0_dispatched(monkeypatch, rules_yaml, capsys):
    ev = {"event_type": "agent.unrelated", "event_id": "E3"}
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(ev)))
    rc = cli.main(["fire"])
    assert rc == 0
    assert "dispatched 0" in capsys.readouterr().out


# ---- test-rule ------------------------------------------------------------

def test_test_rule_dry_run(monkeypatch, rules_yaml, capsys):
    ev = {"event_type": "agent.session_end", "summary": "S"}
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(ev)))
    rc = cli.main(["test-rule"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "rule=demo" in out
    assert "runner=noop" in out
    assert "echo S" in out


def test_test_rule_no_match(monkeypatch, rules_yaml, capsys):
    ev = {"event_type": "X"}
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(ev)))
    cli.main(["test-rule"])
    assert "no match" in capsys.readouterr().out


# ---- replay ---------------------------------------------------------------

def test_replay_event_id_not_found(rules_yaml, fhub_home, capsys):
    rc = cli.main(["replay", "NO_SUCH"])
    assert rc == 2


def test_replay_finds_event(rules_yaml, fhub_home, capsys):
    # 在 journal 当日 jsonl 里写一条
    from feishu_hub import journal
    journal.append({
        "event_type": "agent.session_end",
        "event_id": "FIND_ME",
        "summary": "hi",
    })
    rc = cli.main(["replay", "FIND_ME"])
    assert rc == 0
    assert "dispatched 1" in capsys.readouterr().out


# ---- parser ---------------------------------------------------------------

def test_parser_unknown_cmd_exits():
    with pytest.raises(SystemExit):
        cli.main(["banana"])


def test_build_dctx_loads_rules(rules_yaml, fhub_home):
    dctx = cli._build_dctx(None)
    names = [r.name for r in dctx.rules]
    assert "demo" in names


def test_build_dctx_missing_rules_returns_empty(fhub_home):
    dctx = cli._build_dctx(None)
    assert list(dctx.rules) == []


def test_build_dctx_max_depth_passthrough(rules_yaml, fhub_home):
    dctx = cli._build_dctx(None, max_depth=7)
    assert dctx.max_depth == 7


# ---- checkpoint ---------------------------------------------------------

def test_checkpoint_save_and_load(fhub_home):
    assert cli._load_checkpoint() is None
    cli._save_checkpoint("EVT_X")
    assert cli._load_checkpoint() == "EVT_X"


def test_checkpoint_save_is_atomic(fhub_home):
    cli._save_checkpoint("EVT_A")
    # 应该不留 .tmp
    p = cli._checkpoint_path()
    assert p.exists()
    assert not p.with_suffix(p.suffix + ".tmp").exists()


def test_replay_pending_finds_and_resumes_after(tmp_path):
    """checkpoint 在文件第二行 → 应定位到第三行起。"""
    p = tmp_path / "j.jsonl"
    p.write_text(
        '{"event_id":"A"}\n'
        '{"event_id":"B"}\n'
        '{"event_id":"C"}\n',
        encoding="utf-8",
    )
    fh = p.open("r")
    assert cli._replay_pending(fh, "B") is True
    line = fh.readline()
    assert '"event_id":"C"' in line


def test_replay_pending_missing_checkpoint_replays_all(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text('{"event_id":"X"}\n{"event_id":"Y"}\n', encoding="utf-8")
    fh = p.open("r")
    found = cli._replay_pending(fh, "NEVER")
    assert found is False
    # 从头读，能拿到所有事件
    assert '"event_id":"X"' in fh.readline()


def test_replay_pending_no_checkpoint_seeks_eof(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text('{"event_id":"X"}\n', encoding="utf-8")
    fh = p.open("r")
    cli._replay_pending(fh, None)
    assert fh.readline() == ""  # 已在 EOF


# ---- helpers --------------------------------------------------------------

class _Stdin:
    def __init__(self, s: str):
        self._s = s

    def read(self) -> str:
        return self._s
