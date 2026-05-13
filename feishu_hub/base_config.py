"""config/bases/*.yaml 加载与索引。

设计：docs/superpowers/specs/2026-05-14-m4a-base-schema-design.md §4.2
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import yaml

DEFAULT_DIR = Path("config/bases")
REQUIRED = ("role", "base_token", "table_id", "stage_to_bot")


@dataclass(frozen=True)
class BaseConfig:
    role: str
    base_token: str
    table_id: str
    stage_to_bot: Dict[str, str]
    output_mirror: Dict[str, str] = field(default_factory=dict)


def load_all(root: Path = DEFAULT_DIR) -> List[BaseConfig]:
    out: List[BaseConfig] = []
    if not root.exists():
        return out
    for f in sorted(root.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        missing = [k for k in REQUIRED if k not in data]
        if missing:
            raise ValueError(f"{f}: missing required fields {missing}")
        out.append(BaseConfig(
            role=data["role"],
            base_token=data["base_token"],
            table_id=data["table_id"],
            stage_to_bot=dict(data["stage_to_bot"]),
            output_mirror=dict(data.get("output_mirror") or {}),
        ))
    return out


def resolve_by_role(configs: List[BaseConfig], role: str) -> Optional[BaseConfig]:
    return next((c for c in configs if c.role == role), None)


def resolve_by_base_token(configs: List[BaseConfig], base_token: str) -> Optional[BaseConfig]:
    return next((c for c in configs if c.base_token == base_token), None)
