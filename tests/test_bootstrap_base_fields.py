"""Unit tests for scripts/bootstrap_base_fields.py — mock subprocess.run."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "bootstrap_base_fields.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "bootstrap_base_fields", SCRIPT_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap_base_fields"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


bbf = _load_module()


class _FakeRun:
    """Programmable subprocess.run stand-in.

    Each call pops the next response from `responses`; records argv.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, capture_output=True, text=True, timeout=30):
        self.calls.append(argv)
        if not self.responses:
            raise AssertionError(f"unexpected lark-cli call: {argv!r}")
        rc, stdout = self.responses.pop(0)
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")


def _write_cfg(tmp_path: Path, **extra) -> Path:
    p = tmp_path / "role.yaml"
    body = {"role": "测试角色", "base_token": "btXXX", "table_id": "tblYYY"}
    body.update(extra)
    p.write_text(
        "\n".join(f"{k}: {v}" for k, v in body.items()),
        encoding="utf-8",
    )
    return p


def test_bootstrap_all_missing_creates_everything(tmp_path):
    cfg = _write_cfg(tmp_path)
    # 1) field-list -> empty
    # 2) field-create x3 (运行状态 / 产物 / _last_writer_marker)
    # 3) view-list -> empty
    # 4) view-create -> returns view_id
    # 5) view-set-filter -> ok
    responses = [
        (0, json.dumps({"data": {"fields": []}})),
        (0, json.dumps({"data": {}})),
        (0, json.dumps({"data": {}})),
        (0, json.dumps({"data": {}})),
        (0, json.dumps({"data": {"views": []}})),
        (0, json.dumps({"data": {"view": {"view_id": "vewABC"}}})),
        (0, json.dumps({"data": {}})),
    ]
    fake = _FakeRun(responses)
    with patch("subprocess.run", side_effect=fake):
        rc = bbf.bootstrap(cfg)
    assert rc == 0
    # 7 calls total
    assert len(fake.calls) == 7
    # Verify the field-create payloads
    field_create_calls = [c for c in fake.calls if "+field-create" in c]
    assert len(field_create_calls) == 3
    names = []
    for argv in field_create_calls:
        idx = argv.index("--json")
        names.append(json.loads(argv[idx + 1])["name"])
    assert names == ["运行状态", "产物", "_last_writer_marker"]
    # Verify select payload shape
    select_call = field_create_calls[0]
    payload = json.loads(select_call[select_call.index("--json") + 1])
    assert payload["type"] == "select"
    assert payload["multiple"] is False
    assert [o["name"] for o in payload["options"]] == [
        "idle", "running", "done", "aborted", "failed",
    ]
    # Verify view-create payload
    vc = [c for c in fake.calls if "+view-create" in c][0]
    vpayload = json.loads(vc[vc.index("--json") + 1])
    assert vpayload == {"name": "运行中", "type": "grid"}
    # Verify filter payload
    vf = [c for c in fake.calls if "+view-set-filter" in c][0]
    fpayload = json.loads(vf[vf.index("--json") + 1])
    assert fpayload == {
        "logic": "and",
        "conditions": [["运行状态", "intersects", ["running"]]],
    }
    # And view-id flows through
    assert "vewABC" in vf


def test_bootstrap_idempotent_when_all_exist(tmp_path):
    cfg = _write_cfg(tmp_path)
    existing_fields = [
        {"name": "运行状态"},
        {"name": "产物"},
        {"name": "_last_writer_marker"},
    ]
    responses = [
        (0, json.dumps({"data": {"fields": existing_fields}})),
        (0, json.dumps({"data": {"views": [
            {"name": "运行中", "view_id": "vewOLD"},
        ]}})),
    ]
    fake = _FakeRun(responses)
    with patch("subprocess.run", side_effect=fake):
        rc = bbf.bootstrap(cfg)
    assert rc == 0
    # Only 2 calls: field-list + view-list; nothing created
    assert len(fake.calls) == 2
    assert "+field-create" not in [a for call in fake.calls for a in call]
    assert "+view-create" not in [a for call in fake.calls for a in call]


def test_bootstrap_creates_only_missing_field(tmp_path):
    cfg = _write_cfg(tmp_path)
    # 运行状态 already exists; the other two missing
    responses = [
        (0, json.dumps({"data": {"fields": [{"name": "运行状态"}]}})),
        (0, json.dumps({"data": {}})),  # create 产物
        (0, json.dumps({"data": {}})),  # create _last_writer_marker
        (0, json.dumps({"data": {"views": [
            {"name": "运行中", "view_id": "vewOLD"},
        ]}})),
    ]
    fake = _FakeRun(responses)
    with patch("subprocess.run", side_effect=fake):
        rc = bbf.bootstrap(cfg)
    assert rc == 0
    field_create_calls = [c for c in fake.calls if "+field-create" in c]
    assert len(field_create_calls) == 2
    created = []
    for argv in field_create_calls:
        created.append(json.loads(argv[argv.index("--json") + 1])["name"])
    assert created == ["产物", "_last_writer_marker"]


def test_bootstrap_missing_token_returns_error(tmp_path, capsys):
    p = tmp_path / "broken.yaml"
    p.write_text("role: x\n", encoding="utf-8")
    rc = bbf.bootstrap(p)
    assert rc == 1
    err = capsys.readouterr().err
    assert "missing base_token" in err


def test_create_view_extracts_view_id_from_alt_shape():
    """Defensive: response shape might be data.views[0].view_id."""
    responses = [
        (0, json.dumps({"data": {"views": [{"view_id": "vewALT"}]}})),
    ]
    fake = _FakeRun(responses)
    with patch("subprocess.run", side_effect=fake):
        vid = bbf.create_view("bt", "tbl", "X")
    assert vid == "vewALT"
