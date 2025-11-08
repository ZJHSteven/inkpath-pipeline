"""cli.py
==========
命令行入口，串联 SVG 排版与后处理两个子命令。设计目标：
- 以 argparse 提供自描述式参数；
- 统一加载 config.json 作为默认值，CLI 只覆盖用户显式传入的部分；
- 将具体实现委托给对应模块，保持命令行层逻辑清晰。"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, Iterable, Optional

from .config import ConfigError, load_config
from .svg_font import LayoutParams, SUPPORTED_DIRECTIONS, export_text
from .gcode_post import PostParams, post_process


def build_parser() -> argparse.ArgumentParser:
    """构造顶级命令解析器。"""

    parser = argparse.ArgumentParser(description="GRBL 写字机流水线 CLI")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志，便于排查排版细节")
    subparsers = parser.add_subparsers(dest="command", required=True)

    layout_parser = subparsers.add_parser("layout", help="将汉字排版为 SVG")
    layout_parser.add_argument("--text", help="直接输入要排版的文字，UTF-8 编码")
    layout_parser.add_argument("--text-file", type=Path, help="从文本文件读取内容")
    layout_parser.add_argument("--font-svg", type=Path, required=True, help="字体 SVG 文件路径")
    layout_parser.add_argument("--output", type=Path, required=True, help="输出 SVG 路径")
    layout_parser.add_argument("--direction", choices=SUPPORTED_DIRECTIONS, help="排版方向，默认为 config.json 中的设置")
    layout_parser.add_argument("--cell-size", type=float, help="每个字的格子大小，单位 mm")
    layout_parser.add_argument("--line-spacing", type=float, help="行距系数 0~1")
    layout_parser.add_argument("--char-spacing", type=float, help="字距系数 0~1")
    layout_parser.add_argument("--page-width", type=float, help="页面宽度 mm，默认 A4 210")
    layout_parser.add_argument("--page-height", type=float, help="页面高度 mm，默认 A4 297")
    layout_parser.add_argument("--font-units", type=float, help="字体 units-per-em，默认 1000")

    post_parser = subparsers.add_parser("post", help="对现有 G-code 进行蘸墨/换纸后处理")
    post_parser.add_argument("--input", type=Path, required=True, help="原始 gcode 文件")
    post_parser.add_argument("--output", type=Path, required=True, help="输出 gcode 文件")
    post_parser.add_argument("--insert-n", type=int, help="每多少条绘制指令插入一次蘸墨")
    post_parser.add_argument("--paper-every", type=int, help="每蘸几次墨插一次换纸")
    post_parser.add_argument("--pen-up", type=float, help="抬笔高度覆盖配置")
    post_parser.add_argument("--pen-down", type=float, help="落笔高度覆盖配置")
    post_parser.add_argument("--feedrate", type=float, help="缺省进给速度覆盖配置")

    return parser


def main(argv: Optional[Iterable[str]] = None) -> None:
    """程序入口：解析参数 -> 调用对应子命令。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    if args.command == "layout":
        _handle_layout(args)
    elif args.command == "post":
        _handle_post(args)
    else:  # pragma: no cover - 理论上不会走到
        parser.error("未知命令")


def _handle_layout(args: argparse.Namespace) -> None:
    """处理 layout 子命令：读取文本 + 配置，调用 svg_font 导出。"""

    config = load_config()
    text = _load_text(args)
    layout_cfg = config["layout"]
    page_cfg = config["page"]

    params = LayoutParams(
        text=text,
        font_path=args.font_svg,
        output_path=args.output,
        page_width=args.page_width or page_cfg["width_mm"],
        page_height=args.page_height or page_cfg["height_mm"],
        cell_size=args.cell_size or layout_cfg["cell_size_mm"],
        char_spacing_ratio=args.char_spacing if args.char_spacing is not None else layout_cfg["char_spacing_ratio"],
        line_spacing_ratio=args.line_spacing if args.line_spacing is not None else layout_cfg["line_spacing_ratio"],
        direction=args.direction or layout_cfg["direction"],
        font_units_per_em=args.font_units or layout_cfg["font_units_per_em"],
    )

    result = export_text(params)
    _print_missing_table(result.missing_chars)
    logging.info("共排版 %d 个字符，缺字 %d 个", result.total_chars, len(result.missing_chars))


def _handle_post(args: argparse.Namespace) -> None:
    """处理 post 子命令：装配参数后调用 gcode_post。"""

    config = load_config()
    plotter_cfg = config["plotter"]
    gcode_cfg = config["gcode"]
    macro_cfg = config["macros"]
    positions = config.get("positions", {})

    params = PostParams(
        input_path=args.input,
        output_path=args.output,
        pen_up_z=args.pen_up if args.pen_up is not None else plotter_cfg["pen_up_z"],
        pen_down_z=args.pen_down if args.pen_down is not None else plotter_cfg["pen_down_z"],
        insert_every_n_moves=args.insert_n or gcode_cfg["insert_every_n_moves"],
        insert_every_n_ink=args.paper_every or gcode_cfg["insert_every_n_ink"],
        default_feedrate=args.feedrate or gcode_cfg["default_feedrate"],
        ink_macro=macro_cfg["ink_macro"],
        paper_macro=macro_cfg["paper_macro"],
        macro_context=_build_macro_context(plotter_cfg, positions),
    )

    result = post_process(params)
    print(
        f"已插入蘸墨 {result.ink_times} 次，换纸 {result.paper_times} 次，输出 {result.total_lines} 行 -> {result.output_path}"
    )
    logging.info("G-code 后处理完成")


def _load_text(args: argparse.Namespace) -> str:
    """优先使用 --text，其次读取 --text-file。"""

    if args.text:
        return args.text
    if args.text_file:
        return args.text_file.read_text(encoding="utf-8")
    raise ConfigError("必须通过 --text 或 --text-file 提供内容")


def _print_missing_table(missing: Iterable[str]) -> None:
    """将缺字信息以表格样式输出，便于快速对照。"""

    missing_list = list(missing)
    if not missing_list:
        print("所有字符都在字体中找到，无缺字。")
        return

    unique_chars = sorted(set(missing_list))
    print("缺字统计（使用方框占位）：")
    print("+------+----------+")
    print("| 序号 | 字符     |")
    print("+------+----------+")
    for idx, char in enumerate(unique_chars, 1):
        safe_char = char if char.strip() else "(空白)"
        print(f"| {idx:>4} | {safe_char:<8} |")
    print("+------+----------+")


def _build_macro_context(plotter_cfg: Dict[str, float], positions_cfg: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """将配置转换成 format_map 可以直接使用的字典。"""

    ink = positions_cfg.get("ink", {})
    paper = positions_cfg.get("paper", {})
    return {
        "pen_up_z": plotter_cfg.get("pen_up_z", 0.0),
        "pen_down_z": plotter_cfg.get("pen_down_z", 0.0),
        "safe_z": plotter_cfg.get("safe_z", plotter_cfg.get("pen_up_z", 0.0)),
        "ink_x": ink.get("x", 0.0),
        "ink_y": ink.get("y", 0.0),
        "paper_x": paper.get("x", 0.0),
        "paper_y": paper.get("y", 0.0),
    }
