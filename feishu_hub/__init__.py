"""feishu_hub — 机器级共享办公中台（围绕 lark-cli 的 journal + 聚合层）。

设计约束：本包除 ``llm_summary`` 子模块外，不得 import GA 任何模块
（ga / agent_loop / mykey / bbs / frontends）。详见
``docs/FEISHU_OFFICE_HUB_DESIGN_V2.md`` §10。
"""

__all__ = ["journal", "redact", "remoterefs"]
__version__ = "0.1.0"
SCHEMA_VERSION = 1
