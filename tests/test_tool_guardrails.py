import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolCallSignature,
    classify_tool_failure,
    canonical_tool_args,
)


class SignatureTests(unittest.TestCase):
    def test_same_args_same_signature(self):
        a = ToolCallSignature.from_call("file_read", {"path": "/tmp/a", "n": 1})
        b = ToolCallSignature.from_call("file_read", {"n": 1, "path": "/tmp/a"})
        self.assertEqual(a, b)

    def test_different_args_different_signature(self):
        a = ToolCallSignature.from_call("file_read", {"path": "/tmp/a"})
        b = ToolCallSignature.from_call("file_read", {"path": "/tmp/b"})
        self.assertNotEqual(a, b)


class CanonicalArgsTests(unittest.TestCase):
    def test_sorted_keys(self):
        self.assertEqual(canonical_tool_args({"b": 2, "a": 1}), '{"a":1,"b":2}')

    def test_non_mapping_raises(self):
        with self.assertRaises(TypeError):
            canonical_tool_args([1, 2, 3])  # type: ignore[arg-type]


class FailureClassifierTests(unittest.TestCase):
    def test_code_run_nonzero_exit_is_failure(self):
        failed, tag = classify_tool_failure("code_run", '{"exit_code": 1, "stdout": ""}')
        self.assertTrue(failed)
        self.assertIn("exit 1", tag)

    def test_code_run_zero_exit_is_success(self):
        failed, _ = classify_tool_failure("code_run", '{"exit_code": 0, "stdout": "ok"}')
        self.assertFalse(failed)

    def test_error_string_marks_failure(self):
        failed, _ = classify_tool_failure("file_read", 'Error: not found')
        self.assertTrue(failed)

    def test_none_result_not_failure(self):
        failed, _ = classify_tool_failure("file_read", None)
        self.assertFalse(failed)


class WarnOnlyControllerTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = ToolCallGuardrailController(ToolCallGuardrailConfig(warnings_enabled=True, hard_stop_enabled=False))

    def test_warns_after_repeated_identical_failures(self):
        for _ in range(2):
            d = self.ctrl.after_call("file_read", {"path": "/x"}, "Error: nope", failed=True)
        self.assertEqual(d.action, "warn")
        self.assertEqual(d.code, "repeated_exact_failure_warning")

    def test_warns_on_idempotent_repeat_same_result(self):
        for _ in range(2):
            d = self.ctrl.after_call("file_read", {"path": "/x"}, '{"content": "same"}', failed=False)
        self.assertEqual(d.action, "warn")
        self.assertEqual(d.code, "idempotent_no_progress_warning")

    def test_no_warn_on_mutating_tool_repeat(self):
        for _ in range(5):
            d = self.ctrl.after_call("code_run", {"script": "echo ok"}, '{"exit_code": 0, "stdout": "ok"}', failed=False)
        self.assertEqual(d.action, "allow")

    def test_before_call_does_not_block_when_hard_stop_off(self):
        for _ in range(10):
            self.ctrl.after_call("file_read", {"path": "/x"}, "Error", failed=True)
        d = self.ctrl.before_call("file_read", {"path": "/x"})
        self.assertEqual(d.action, "allow")


class HardStopControllerTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = ToolCallGuardrailController(ToolCallGuardrailConfig(hard_stop_enabled=True))

    def test_block_after_exact_failure_threshold(self):
        for _ in range(self.ctrl.config.exact_failure_block_after):
            self.ctrl.after_call("web_scan", {"q": "foo"}, "Error", failed=True)
        d = self.ctrl.before_call("web_scan", {"q": "foo"})
        self.assertEqual(d.action, "block")
        self.assertEqual(d.code, "repeated_exact_failure_block")

    def test_halt_after_same_tool_failure_threshold(self):
        last = None
        for i in range(self.ctrl.config.same_tool_failure_halt_after):
            last = self.ctrl.after_call("web_scan", {"q": f"q{i}"}, "Error", failed=True)
        self.assertEqual(last.action, "halt")

    def test_no_progress_block_for_idempotent_with_same_result(self):
        for _ in range(self.ctrl.config.no_progress_block_after):
            self.ctrl.after_call("file_read", {"path": "/x"}, '{"content": "frozen"}', failed=False)
        d = self.ctrl.before_call("file_read", {"path": "/x"})
        self.assertEqual(d.action, "block")
        self.assertEqual(d.code, "idempotent_no_progress_block")

    def test_success_resets_failure_counters(self):
        for _ in range(3):
            self.ctrl.after_call("file_read", {"path": "/x"}, "Error", failed=True)
        self.ctrl.after_call("file_read", {"path": "/x"}, '{"content": "ok"}', failed=False)
        d = self.ctrl.before_call("file_read", {"path": "/x"})
        self.assertEqual(d.action, "allow")


class ConfigEnvTests(unittest.TestCase):
    def test_from_env_defaults_to_warn_only(self):
        import os
        os.environ.pop("GA_TOOLGUARD", None); os.environ.pop("GA_TOOLGUARD_HARDSTOP", None)
        cfg = ToolCallGuardrailConfig.from_env()
        self.assertTrue(cfg.warnings_enabled)
        self.assertFalse(cfg.hard_stop_enabled)

    def test_from_env_hard_stop_opt_in(self):
        import os
        os.environ["GA_TOOLGUARD_HARDSTOP"] = "1"
        try:
            cfg = ToolCallGuardrailConfig.from_env()
            self.assertTrue(cfg.hard_stop_enabled)
        finally:
            os.environ.pop("GA_TOOLGUARD_HARDSTOP", None)


if __name__ == "__main__":
    unittest.main()
