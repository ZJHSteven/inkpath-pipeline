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
from typing import Iterable, Optional

from .config import ConfigError, load_config
from .svg_font import LayoutParams, SUPPORTED_DIRECTIONS, export_text
from .gcode_post import JobParams, PostParams, build_macro_context, post_process


def build_parser() -> argparse.ArgumentParser:
    """构造顶级命令解析器。"""

    parser = argparse.ArgumentParser(description="GRBL 写字机流水线 CLI")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志，便于排查排版细节")
    subparsers = parser.add_subparsers(dest="command", required=True)

    layout_parser = subparsers.add_parser("layout", help="将汉字排版为 SVG")
    layout_parser.add_argument("--text", help="直接输入要排版的文字，UTF-8 编码")
    layout_parser.add_argument("--text-file", type=Path, help="从文本文件读取内容")
    layout_parser.add_argument("--font-svg", type=Path, help="字体 SVG 文件路径，默认读取 config.paths.layout.font_svg")
    layout_parser.add_argument("--output", type=Path, help="输出 SVG 路径，默认读取 config.paths.layout.output_svg")
    layout_parser.add_argument("--direction", choices=SUPPORTED_DIRECTIONS, help="排版方向，默认为 config.json 中的设置")
    layout_parser.add_argument("--cell-size", type=float, help="每个字的格子大小，单位 mm")
    layout_parser.add_argument("--line-spacing", type=float, help="行距系数 0~1")
    layout_parser.add_argument("--char-spacing", type=float, help="字距系数 0~1")
    layout_parser.add_argument("--page-width", type=float, help="页面宽度 mm，默认 A4 210")
    layout_parser.add_argument("--page-height", type=float, help="页面高度 mm，默认 A4 297")
    layout_parser.add_argument("--font-units", type=float, help="字体 units-per-em，默认 1000")

    post_parser = subparsers.add_parser("post", help="将写字/绘画 G-code 合并并自动插入蘸墨/换纸宏")
    post_parser.add_argument("--writing-input", type=Path, help="写字 G-code，默认使用 config.paths.post.writing_input")
    post_parser.add_argument("--drawing-input", type=Path, help="绘画 G-code，默认使用 config.paths.post.drawing_input")
    post_parser.add_argument("--output", type=Path, help="合并后 G-code 输出，默认使用 config.paths.post.merged_output")
    post_parser.add_argument(
        "--writing-mode",
        choices=["off", "marker", "stroke"],
        help="写字蘸墨模式：off=关闭，marker=按标记行，stroke=按笔画计数（默认读取 config.gcode.writing.ink_mode）",
    )
    post_parser.add_argument(
        "--writing-interval", type=int, help="写字 stroke 模式下的笔画阈值，默认读取 config.gcode.writing.stroke_interval"
    )
    post_parser.add_argument(
        "--drawing-interval", type=int, help="绘画阶段笔画阈值，默认读取 config.gcode.drawing.stroke_interval"
    )
    post_parser.add_argument(
        "--marker-token",
        help="写字 marker 模式识别的整行标记字符串，默认读取 config.gcode.marker.token",
    )
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
    layout_paths = config.get("paths", {}).get("layout", {})
    font_path = _pick_path(args.font_svg, layout_paths.get("font_svg"), "--font-svg")
    output_path = _pick_path(args.output, layout_paths.get("output_svg"), "--output")

    params = LayoutParams(
        text=text,
        font_path=font_path,
        output_path=output_path,
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
    post_paths = config.get("paths", {}).get("post", {})

    writing_path = _pick_path(args.writing_input, post_paths.get("writing_input"), "--writing-input")
    drawing_path = _pick_path(args.drawing_input, post_paths.get("drawing_input"), "--drawing-input")
    output_path = _pick_path(args.output, post_paths.get("merged_output"), "--output")

    writing_cfg = gcode_cfg.get("writing", {})
    drawing_cfg = gcode_cfg.get("drawing", {})
    marker_cfg = gcode_cfg.get("marker", {})
    writing_mode = (args.writing_mode or writing_cfg.get("ink_mode", "marker")).lower()
    writing_interval = args.writing_interval or writing_cfg.get("stroke_interval")
    drawing_interval = args.drawing_interval or drawing_cfg.get("stroke_interval")
    marker_token = args.marker_token or marker_cfg.get("token", ";#AUTO_INK#")

    params = PostParams(
        writing=JobParams(
            name="写字",
            input_path=writing_path,
            ink_mode=writing_mode,
            stroke_interval=writing_interval,
        ),
        drawing=JobParams(
            name="绘画",
            input_path=drawing_path,
            ink_mode="stroke",
            stroke_interval=drawing_interval,
        ),
        output_path=output_path,
        pen_up_z=args.pen_up if args.pen_up is not None else plotter_cfg["pen_up_z"],
        pen_down_z=args.pen_down if args.pen_down is not None else plotter_cfg["pen_down_z"],
        default_feedrate=args.feedrate if args.feedrate is not None else gcode_cfg["default_feedrate"],
        ink_macro=macro_cfg["ink_macro"],
        paper_macro=macro_cfg["paper_macro"],
        macro_context=build_macro_context(plotter_cfg, positions),
        marker_token=marker_token,
    )

    result = post_process(params)
    print(
        f"写字蘸墨 {result.writing_ink_times} 次，绘画蘸墨 {result.drawing_ink_times} 次，换纸 {result.paper_times} 次，"
        f"输出 {result.total_lines} 行 -> {result.output_path}"
    )
    logging.info("G-code 后处理完成")


def _pick_path(cli_value: Path | None, config_value: str | None, flag_name: str) -> Path:
    """优先使用 CLI 传入路径，否则回落到 config 中的默认值。"""

    candidate = _to_path(cli_value) or _to_path(config_value)
    if candidate is None:
        raise ConfigError(f"请通过 {flag_name} 指定路径，或在 config.paths 中设置默认值")
    return candidate


def _to_path(raw: Path | str | None) -> Path | None:
    """把 CLI 参数或配置字符串统一转换为 Path，便于后续复用。"""

    if raw is None:
        return None
    if isinstance(raw, Path):
        return raw.expanduser()
    return Path(raw).expanduser()


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
