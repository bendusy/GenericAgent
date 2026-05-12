"""frontends.lark_tools_adapter 单测：注入 + 输入校验 + 错误归一化。"""
import datetime as _dt
import sys
import types
from unittest.mock import patch

import pytest

yaml = pytest.importorskip("yaml")


@pytest.fixture
def fake_ga(monkeypatch):
    """临时塑造一个 'ga' + 'agent_loop' 模块，注入到 sys.modules。"""
    class _StepOutcome:
        def __init__(self, payload, next_prompt="", should_exit=False):
            self.payload = payload
            self.next_prompt = next_prompt
            self.should_exit = should_exit

    class _Handler:
        pass

    ga_mod = types.ModuleType("ga")
    ga_mod.GenericAgentHandler = _Handler
    al_mod = types.ModuleType("agent_loop")
    al_mod.StepOutcome = _StepOutcome
    monkeypatch.setitem(sys.modules, "ga", ga_mod)
    monkeypatch.setitem(sys.modules, "agent_loop", al_mod)
    # 强制重新 install
    monkeypatch.delitem(sys.modules, "frontends.lark_tools_adapter",
                        raising=False)
    import frontends.lark_tools_adapter as lta
    lta.install()
    return {"Handler": _Handler, "StepOutcome": _StepOutcome, "adapter": lta}


def test_install_idempotent(fake_ga):
    H = fake_ga["Handler"]
    fake_ga["adapter"].install()
    fake_ga["adapter"].install()
    assert hasattr(H, "do_daily_report")
    assert hasattr(H, "do_feishu_notify")


def test_do_daily_report_returns_step_outcome(fake_ga):
    H = fake_ga["Handler"]
    from feishu_hub import daily_report as dr
    fake = dr.DailyReport(
        date=_dt.date(2026, 5, 12), title="日报 2026-05-12",
        doc_token="doxcnX", doc_url="https://u",
        folder_token="fldcnM", created=True,
        record_count=2, commit_count=1,
    )
    h = H()
    with patch.object(dr, "generate", return_value=fake) as mk:
        out = H.do_daily_report(h, {"date": "2026-05-12", "no_notify": True}, None)
    assert mk.call_count == 1
    kwargs = mk.call_args.kwargs
    assert kwargs["date"] == _dt.date(2026, 5, 12)
    assert kwargs["notify"] is False
    assert isinstance(out, fake_ga["StepOutcome"])
    assert "新建" in out.payload and "doxcnX" in out.payload


def test_do_daily_report_error_wrapped(fake_ga):
    H = fake_ga["Handler"]
    from feishu_hub import daily_report as dr
    with patch.object(dr, "generate", side_effect=RuntimeError("config bad")):
        out = H.do_daily_report(H(), {}, None)
    assert "daily_report error" in out.payload
    assert "config bad" in out.payload


def test_do_feishu_notify_uses_config_when_no_user_id(fake_ga, monkeypatch, tmp_path):
    from feishu_hub import config as cfgmod, lark_cli
    home = tmp_path / "fhub"
    monkeypatch.setenv(cfgmod.ENV_ROOT, str(home))
    cfgmod.save({"notify_receive_id": "ou_default"}, path=home / "config.yaml")
    H = fake_ga["Handler"]
    with patch.object(lark_cli, "im_send_text", return_value="om_ok") as mk:
        out = H.do_feishu_notify(H(), {"text": "hello"}, None)
    mk.assert_called_once_with(user_id="ou_default", text="hello",
                                idempotency_key=None)
    assert "om_ok" in out.payload


def test_do_feishu_notify_missing_user_id_errors(fake_ga, monkeypatch, tmp_path):
    from feishu_hub import config as cfgmod
    home = tmp_path / "fhub"
    monkeypatch.setenv(cfgmod.ENV_ROOT, str(home))
    cfgmod.save({"notify_receive_id": ""}, path=home / "config.yaml")
    H = fake_ga["Handler"]
    out = H.do_feishu_notify(H(), {"text": "x"}, None)
    assert "user_id missing" in out.payload


def test_do_feishu_notify_missing_text_errors(fake_ga, monkeypatch, tmp_path):
    from feishu_hub import config as cfgmod
    home = tmp_path / "fhub"
    monkeypatch.setenv(cfgmod.ENV_ROOT, str(home))
    cfgmod.save({"notify_receive_id": "ou_x"}, path=home / "config.yaml")
    H = fake_ga["Handler"]
    out = H.do_feishu_notify(H(), {}, None)
    assert "text required" in out.payload


def test_do_feishu_notify_wraps_lark_error(fake_ga, monkeypatch, tmp_path):
    from feishu_hub import config as cfgmod, lark_cli
    home = tmp_path / "fhub"
    monkeypatch.setenv(cfgmod.ENV_ROOT, str(home))
    cfgmod.save({"notify_receive_id": "ou_x"}, path=home / "config.yaml")
    H = fake_ga["Handler"]
    with patch.object(lark_cli, "im_send_text",
                      side_effect=lark_cli.LarkCLIError(1234, "blocked", ["im"])):
        out = H.do_feishu_notify(H(), {"text": "x"}, None)
    assert "1234" in out.payload and "blocked" in out.payload


def test_install_without_ga_is_safe(monkeypatch, capsys):
    """GA 缺失时 install() 不应抛异常。"""
    monkeypatch.delitem(sys.modules, "ga", raising=False)
    monkeypatch.delitem(sys.modules, "agent_loop", raising=False)
    monkeypatch.delitem(sys.modules, "frontends.lark_tools_adapter",
                        raising=False)
    # 用 import hook 阻挡 'ga' import
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__
    import builtins

    def block(name, *a, **kw):
        if name in ("ga", "agent_loop"):
            raise ModuleNotFoundError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", block)
    import frontends.lark_tools_adapter as lta  # 重新 import 触发 install
    # 应该平稳跳过；不抛
    captured = capsys.readouterr()
    assert "install skipped" in captured.out
