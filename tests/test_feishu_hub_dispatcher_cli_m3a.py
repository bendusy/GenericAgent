"""M3.A 契约：cli 模块不再 import bitable_writer / 不再有 tail 子命令。"""
import importlib


def test_cli_does_not_import_bitable_writer():
    cli = importlib.import_module("feishu_hub.dispatcher.cli")
    # 模块 namespace 不应含 bitable_writer
    assert not hasattr(cli, "bitable_writer"), \
        "M3.A: cli.py 不应再 import bitable_writer"


def test_cli_has_no_tail_subcommand():
    from feishu_hub.dispatcher import cli
    parser = cli.build_parser()
    # 子命令列表里不应有 tail
    subparsers_action = next(
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    )
    assert "tail" not in subparsers_action.choices, \
        "M3.A: cli 不应保留 tail 子命令（违反协同模型）"
    # fire / replay 必须仍在
    assert "fire" in subparsers_action.choices
    assert "replay" in subparsers_action.choices
