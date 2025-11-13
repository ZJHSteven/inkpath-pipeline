"""svg_font.py
================
该模块负责：
1. 解析 SVG 字体文件中的 glyph 描述；
2. 按 A4 画幅与写字机坐标习惯排布文本；
3. 生成可直接丢进 Inkscape 复查的 SVG 输出；
4. 统计缺字并交由 CLI/GUI 提示用户。

实现策略：
- 统一把排版参数封装成数据类，方便 CLI 与 GUI 共享；
- 将异常（字体解析失败等）单独用 FontParseError 表达，主流程可一次性捕获；
- 关键几何运算都写在独立函数里，降低未来替换排版策略的成本。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


class FontParseError(RuntimeError):
    """字体 SVG 解析失败时抛出的统一异常。"""


@dataclass(frozen=True)
class LayoutParams:
    """排版所需的核心输入参数集合。"""

    text: str
    font_path: Path
    output_path: Path
    page_width: float
    page_height: float
    cell_size: float
    char_spacing_ratio: float
    line_spacing_ratio: float
    direction: str  # horizontal / vertical
    font_units_per_em: float


@dataclass(frozen=True)
class LayoutResult:
    """排版完成后的结果快照，便于 CLI 打印或 GUI 展示。"""

    missing_chars: List[str]
    total_chars: int
    output_path: Path


Direction = str
SUPPORTED_DIRECTIONS: Tuple[Direction, ...] = ("horizontal", "vertical")


def export_text(params: LayoutParams) -> LayoutResult:
    """主入口：执行排版并写入 SVG 文件。"""

    _validate_params(params)
    glyph_map = _load_glyphs(params.font_path)
    layout_nodes, missing = _build_layout(params, glyph_map)
    svg_root = _build_svg_root(params, layout_nodes)
    svg_bytes = ET.tostring(svg_root, encoding="utf-8", xml_declaration=True)
    params.output_path.write_bytes(svg_bytes)
    logger.info("SVG 已输出至 %s", params.output_path)
    return LayoutResult(missing_chars=missing, total_chars=len(params.text), output_path=params.output_path)


@dataclass(frozen=True)
class _GlyphPlacement:
    """内部结构体：记录单个字的位移与缩放。"""

    index: int
    char: str
    path_data: str
    translate_x: float
    translate_y: float
    scale: float


def _validate_params(params: LayoutParams) -> None:
    """对所有浮点与路径参数做基础校验，提前失败更易排查。"""

    if params.direction not in SUPPORTED_DIRECTIONS:
        raise ValueError(f"direction 必须是 {SUPPORTED_DIRECTIONS} 之一")
    if params.cell_size <= 0:
        raise ValueError("cell_size 必须为正数")
    if params.page_width <= 0 or params.page_height <= 0:
        raise ValueError("纸张尺寸必须为正数")
    if params.font_units_per_em <= 0:
        raise ValueError("font_units_per_em 必须大于 0")
    if not params.font_path.exists():
        raise FileNotFoundError(f"找不到字体文件：{params.font_path}")
    params.output_path.parent.mkdir(parents=True, exist_ok=True)


def _load_glyphs(font_path: Path) -> Dict[str, str]:
    """读取 SVG 字体，返回 unicode->path 的映射。"""

    try:
        tree = ET.parse(font_path)
    except ET.ParseError as exc:  # pragma: no cover - 仅在文件损坏时触发
        raise FontParseError(f"无法解析字体：{exc}") from exc
    root = tree.getroot()
    glyph_map: Dict[str, str] = {}
    for glyph in root.iter():
        if glyph.tag.endswith("glyph"):
            unicode_char = glyph.attrib.get("unicode")
            path_data = glyph.attrib.get("d")
            if unicode_char and path_data:
                glyph_map[unicode_char] = path_data
    if not glyph_map:
        raise FontParseError("字体文件中未找到任何 glyph 节点")
    logger.debug("加载 %d 个 glyph", len(glyph_map))
    return glyph_map


def _build_layout(params: LayoutParams, glyph_map: Dict[str, str]) -> Tuple[List[_GlyphPlacement], List[str]]:
    """根据方向/间距把文本拆成一个个带坐标的字。"""

    placements: List[_GlyphPlacement] = []
    missing: List[str] = []
    char_step = params.cell_size * (1.0 + params.char_spacing_ratio)
    line_step = params.cell_size * (1.0 + params.line_spacing_ratio)
    if char_step <= 0 or line_step <= 0:
        raise ValueError("间距系数导致步长无效")

    max_cols = max(1, int(params.page_width // char_step))
    max_rows = max(1, int(params.page_height // line_step))
    scale = params.cell_size / params.font_units_per_em
    col = 0
    row = 0

    for idx, char in enumerate(params.text):
        if char == "\n":  # 手动换行
            col = 0
            row += 1
            continue
        path_data = glyph_map.get(char)
        if path_data is None:
            missing.append(char)
            path_data = _fallback_path(params.font_units_per_em)
        translate_x, translate_y, col, row = _position_for_char(
            col, row, params.direction, max_cols, max_rows, char_step, line_step
        )
        # 为了保证字符整体留在 y>=0 的范围内，我们把基准点放到格子底部。
        baseline_y = translate_y + params.cell_size
        placement = _GlyphPlacement(
            index=len(placements) + 1,
            char=char,
            path_data=path_data,
            translate_x=translate_x,
            translate_y=baseline_y,
            scale=scale,
        )
        placements.append(placement)
    return placements, missing


def _position_for_char(
    col: int,
    row: int,
    direction: Direction,
    max_cols: int,
    max_rows: int,
    char_step: float,
    line_step: float,
) -> Tuple[float, float, int, int]:
    """以左上角为原点、屏幕坐标系（x 向右、y 向下）计算当前格子的左上角，同时返回下一个字符的行列索引。

    这里返回的是“格子左上角”而非基线位置，调用方需按需要追加基线偏移。
    ``max_cols``/``max_rows`` 控制自动换行/换列，确保矩阵不越界。
    """

    if direction == "horizontal":
        if col >= max_cols:
            col = 0  # 从下一行第 0 列继续写
            row += 1  # 垂直方向往下移动一整行
        x = col * char_step  # 列号 * 单元宽 = 当前格子的左上角 x
        y = row * line_step  # 行号 * 单元高 = 当前格子的左上角 y
        col += 1  # 准备处理本行下一个字符
    else:  # vertical
        if row >= max_rows:
            row = 0  # ����д��һ�к����һ�ж��˿�ʼ
            col += 1  # x ��������һ����Ԫ����
        rightmost_col_index = max_cols - 1  # ���Ұ�дʱһ����Ҫ�ӵ�һ�������ұ߿�ʼ����λ���ٽ�Ĭ��ֵΪ 0
        column_from_right = rightmost_col_index - col  # ��¼�Ǵ��ұߵڼ���д����ʹ����д˳���������д���ȵ��ұ�
        x = column_from_right * char_step  # ����ǰ�кŵ�����ӳ�䵽��Ļ���ϵ x ����ʹ�������ұ���ȡֵ����
        y = row * line_step  # ����ֱ��ӳ�䵽��ǰ���ӵ����Ͻ� y
        row += 1  # ��ֱ�Ű���һ���ַ���������
    return x, y, col, row


def _fallback_path(units: float) -> str:
    """缺字时用的方框，方便肉眼定位位置。"""

    size = units * 0.9
    margin = units * 0.05
    top = margin
    bottom = margin + size
    return f"M{margin} {top} L{margin} {bottom} L{margin + size} {bottom} L{margin + size} {top} Z"


def _append_page_frame(svg_root: ET.Element, params: LayoutParams) -> None:
    """添加纸框辅助线：左上角 (0,0)，右下角 (page_width, page_height)。"""

    ET.SubElement(
        svg_root,
        "rect",
        attrib={
            "id": "page-frame",
            "x": "0",
            "y": "0",
            "width": f"{params.page_width}",
            "height": f"{params.page_height}",
            "fill": "none",
            "stroke": "#999999",
            "stroke-width": "0.2",
            "vector-effect": "non-scaling-stroke",
        },
    )


def _build_svg_root(params: LayoutParams, nodes: Iterable[_GlyphPlacement]) -> ET.Element:
    """将所有字符组装成 SVG DOM，并补充纸框辅助线。"""

    svg_root = ET.Element(
        "svg",
        attrib={
            "xmlns": "http://www.w3.org/2000/svg",
            "width": f"{params.page_width}mm",
            "height": f"{params.page_height}mm",
            "viewBox": f"0 0 {params.page_width} {params.page_height}",
        },
    )
    _append_page_frame(svg_root, params)
    for placement in nodes:
        group = ET.SubElement(
            svg_root,
            "g",
            attrib={
                "id": f"char-{placement.index:03d}",
                "aria-label": placement.char,
                # 这里 scale 的 y 轴系数取负值，用于把字体坐标系（数学坐标，y 向上）翻回屏幕坐标。
                # 因为 translate_y 已经指向格子底部，所以翻转不会让字符落到负坐标区。
                "transform": (
                    f"translate({placement.translate_x:.3f}, {placement.translate_y:.3f}) "
                    f"scale({placement.scale:.5f}, {-placement.scale:.5f})"
                ),
            },
        )
        ET.SubElement(group, "path", attrib={"d": placement.path_data})
    return svg_root
