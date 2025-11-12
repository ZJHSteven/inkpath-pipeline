"""config.py
===============
该模块集中负责配置文件的读写、默认值生成与错误处理，避免业务模块重复关注磁盘状态。
所有函数都以明确的输入输出设计，确保 CLI 与 GUI 可共享同一套配置逻辑。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


class ConfigError(RuntimeError):
    """配置相关的统一异常，方便主流程捕获并做友好提示。"""


@dataclass(frozen=True)
class ConfigSnapshot:
    """简单的数据类包装，便于在 GUI 中展示当前的配置快照。"""

    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """返回底层 dict 副本，防止调用方直接修改内部状态。"""
        return json.loads(json.dumps(self.data, ensure_ascii=False))


DEFAULT_CONFIG: Dict[str, Any] = {
    "page": {
        "width_mm": 210.0,
        "height_mm": 297.0,
    },
    "layout": {
        "cell_size_mm": 40.0,
        "line_spacing_ratio": 0.2,
        "char_spacing_ratio": 0.1,
        "direction": "horizontal",
        "font_units_per_em": 1000.0,
    },
    "plotter": {
        "pen_up_z": 0.0,
        "pen_down_z": 8.0,
        "safe_z": 1.0,
    },
    "positions": {
        "ink": {"x": 10.0, "y": -10.0},
        "paper": {"x": 0.0, "y": 0.0},
    },
    "macros": {
        "ink_macro": [
            "G0 Z0",
            "G0 X10 Y-10",
            "G1 Z8",
            "G4 P0.5",
            "G0 Z0",
        ],
        "paper_macro": [
            "G0 Z0",
            "G0 X0 Y0",
            "G4 P1.0",
        ],
    },
    "paths": {
        "layout": {
            "font_svg": "assets/fonts/ink_font.svg",
            "output_svg": "artifacts/layout.svg",
        },
        "post": {
            "writing_input": "artifacts/gcode/writing.nc",
            "drawing_input": "artifacts/gcode/drawing.nc",
            "merged_output": "artifacts/gcode/merged.nc",
        },
    },
    "gcode": {
        "default_feedrate": 1000,
        "marker": {
            "token": ";#AUTO_INK#",
        },
        "writing": {
            "ink_mode": "marker",
            "stroke_interval": 40,
        },
        "drawing": {
            "stroke_interval": 80,
        },
    },
}


def ensure_config_file(path: Path = CONFIG_PATH) -> Path:
    """若 config.json 不存在，则写入默认模板，确保后续读操作稳定。"""

    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """加载配置，将缺失字段补齐默认值后返回 dict。"""

    try:
        ensure_config_file(path)
        user_cfg = json.loads(path.read_text(encoding="utf-8"))
        return _deep_merge(DEFAULT_CONFIG, user_cfg)
    except json.JSONDecodeError as exc:  # pragma: no cover - 极端错误路径
        raise ConfigError(f"无法解析配置文件 {path}: {exc}") from exc


def save_config(data: Dict[str, Any], path: Path = CONFIG_PATH) -> None:
    """将配置写回磁盘；写入前做一次 JSON 序列化校验。"""

    try:
        serialized = json.dumps(data, indent=2, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"配置数据无法序列化：{exc}") from exc
    path.write_text(serialized, encoding="utf-8")


def update_config(partial: Dict[str, Any], path: Path = CONFIG_PATH) -> ConfigSnapshot:
    """深度合并新字段并保存，返回新的快照供调用方使用。"""

    config = load_config(path)
    merged = _deep_merge(config, partial)
    save_config(merged, path)
    return ConfigSnapshot(data=merged)


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并 dict，extra 优先；子字典沿用相同策略。"""

    result: Dict[str, Any] = {}
    for key in base.keys() | extra.keys():
        left = base.get(key)
        right = extra.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            result[key] = _deep_merge(left, right)
        elif right is None:
            result[key] = left
        else:
            result[key] = right if right is not None else left
    return result


def snapshot(path: Path = CONFIG_PATH) -> ConfigSnapshot:
    """便捷函数：直接获取当前配置快照。"""

    return ConfigSnapshot(data=load_config(path))
