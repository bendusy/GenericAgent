from unittest.mock import patch

from feishu_hub.stop_hook import run


@patch("feishu_hub.stop_hook.task_writer")
def test_run_creates_task_and_appends(task_writer, capsys):
    task_writer.get_or_create_for_session.return_value = task_writer.TaskRef(
        guid="g-1", url="https://applink.feishu.cn/client/todo/detail?guid=g-1"
    )
    rc = run(
        agent="cc",
        session="sess-1",
        cwd="/repo/foo",
        summary="完成代码审查",
        follower_open_id="ou_x",
    )
    assert rc == 0
    task_writer.get_or_create_for_session.assert_called_once()
    task_writer.append_steps.assert_called_once()


@patch("feishu_hub.stop_hook.task_writer")
@patch("feishu_hub.stop_hook._send_im_fallback")
def test_run_fallback_on_lark_cli_failure(send_im, task_writer):
    from feishu_hub.lark_cli import LarkCLIError

    task_writer.get_or_create_for_session.side_effect = LarkCLIError(
        code=10000, msg="boom", argv=["x"]
    )
    rc = run(
        agent="cc",
        session="s",
        cwd="/r",
        summary="x",
        follower_open_id="ou_x",
    )
    assert rc == 0  # 仍然 0，不阻塞 agent
    send_im.assert_called_once()


@patch("feishu_hub.stop_hook.task_writer")
def test_run_no_follower_skips_task(task_writer, capsys):
    """没配 FEISHU_NOTIFY_TO（follower_open_id 为空）→ 不调 task_writer，直接 exit 0。"""
    rc = run(agent="cc", session="s", cwd="/r", summary="x", follower_open_id="")
    assert rc == 0
    task_writer.get_or_create_for_session.assert_not_called()
