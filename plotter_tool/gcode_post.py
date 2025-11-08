"""gcode_post.py
=================
该模块负责对原始 G-code 进行自动化润色：
1. 在文件开头补齐缺失的进给速度；
2. 按计数插入蘸墨与换纸宏；
3. 确保只有在抬笔高度才执行宏，避免拖笔。

实现细节：
- 通过状态机记录当前 Z 值、笔状态与计数器；
- 所有磁盘 I/O 与字符串处理都集中在该模块，CLI/GUI 仅负责参数收集；
- 宏模板允许使用 `{ink_x}` 这类占位符，便于配置文件直接写坐标。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
import logging
import re

logger = logging.getLogger(__name__)

AXIS_PATTERN = re.compile(r"([XYZF])\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
MOTION_PREFIXES = ("G1", "G01")
COMMENT_PREFIXES = (";", "(")
FLOAT_EPS = 1e-4


class GcodePostError(RuntimeError):
    """后处理失败时的统一异常。"""


@dataclass(frozen=True)
class PostParams:
    """承载后处理所需的全部外部参数。"""

    input_path: Path
    output_path: Path
    pen_up_z: float
    pen_down_z: float
    insert_every_n_moves: int
    insert_every_n_ink: int
    default_feedrate: float
    ink_macro: List[str]
    paper_macro: List[str]
    macro_context: Dict[str, float]


@dataclass(frozen=True)
class PostResult:
    """统计信息，便于 CLI/GUI 打印提示。"""

    ink_times: int
    paper_times: int
    total_lines: int
    output_path: Path


@dataclass
class _State:
    """内部运行时状态，拆分字段方便调试。"""

    current_z: float
    pen_down: bool
    move_counter: int = 0
    ink_counter: int = 0


def post_process(params: PostParams) -> PostResult:
    """读写 G-code 并返回统计结果。"""

    _validate_params(params)
    lines = params.input_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise GcodePostError("输入 G-code 为空")
    lines = _ensure_feedrate(lines, params.default_feedrate)

    state = _State(current_z=params.pen_up_z, pen_down=False)
    output_lines: List[str] = []
    ink_insertions = 0
    paper_insertions = 0

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            output_lines.append(raw_line)
            continue

        cmd = stripped.split()[0].upper()
        z_value = _extract_axis(stripped, "Z")
        if z_value is not None:
            state.current_z = z_value
            state.pen_down = z_value >= params.pen_down_z - FLOAT_EPS

        if cmd.startswith(MOTION_PREFIXES) and _contains_xy(stripped) and state.pen_down:
            state.move_counter += 1

        output_lines.append(raw_line)

        if params.insert_every_n_moves and state.move_counter and state.move_counter % params.insert_every_n_moves == 0:
            ink_insertions += 1
            _inject_macro(
                output_lines,
                params.ink_macro,
                params,
                state,
                note=f"蘸墨 #{ink_insertions}",
            )
            state.move_counter = 0
            if params.insert_every_n_ink and ink_insertions % params.insert_every_n_ink == 0:
                paper_insertions += 1
                _inject_macro(
                    output_lines,
                    params.paper_macro,
                    params,
                    state,
                    note=f"换纸 #{paper_insertions}",
                )

    params.output_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    logger.info("G-code 已写入 %s", params.output_path)
    return PostResult(
        ink_times=ink_insertions,
        paper_times=paper_insertions,
        total_lines=len(output_lines),
        output_path=params.output_path,
    )


def _validate_params(params: PostParams) -> None:
    """基础参数合法性检查。"""

    if not params.input_path.exists():
        raise FileNotFoundError(f"找不到 gcode：{params.input_path}")
    if params.insert_every_n_moves <= 0:
        raise ValueError("insert_every_n_moves 必须为正整数")
    if params.insert_every_n_ink < 0:
        raise ValueError("insert_every_n_ink 不能为负数")
    if params.pen_down_z <= params.pen_up_z:
        raise ValueError("pen_down_z 必须大于 pen_up_z")
    params.output_path.parent.mkdir(parents=True, exist_ok=True)


def _ensure_feedrate(lines: List[str], default_feed: float) -> List[str]:
    """检查前两行是否包含 F，缺失则在首行后补一条 G1 Fxxx。"""

    candidate_indexes: List[int] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            continue
        candidate_indexes.append(idx)
        if len(candidate_indexes) == 2:
            break
    has_feed = any(_has_feedrate(lines[i]) for i in candidate_indexes)
    if has_feed or not candidate_indexes:
        return lines
    insert_pos = candidate_indexes[0] + 1
    injection = f"G1 F{default_feed}"
    logger.debug("欠缺速度指令，已在第 %d 行插入 %s", insert_pos + 1, injection)
    return lines[:insert_pos] + [injection] + lines[insert_pos:]


def _has_feedrate(line: str) -> bool:
    """检测一行是否包含 F 数值。"""

    return any(match.group(1).upper() == "F" for match in AXIS_PATTERN.finditer(line))


def _contains_xy(line: str) -> bool:
    """判断是否包含 X/Y，决定是否计入绘图次数。"""

    line_upper = line.upper()
    return "X" in line_upper or "Y" in line_upper


def _extract_axis(line: str, axis: str) -> float | None:
    """提取指定轴的数值，未找到返回 None。"""

    axis = axis.upper()
    for match in AXIS_PATTERN.finditer(line):
        if match.group(1).upper() == axis:
            return float(match.group(2))
    return None


def _inject_macro(
    buffer: List[str],
    macro_lines: Iterable[str],
    params: PostParams,
    state: _State,
    note: str,
) -> None:
    """把宏指令插入输出缓冲。"""

    if not macro_lines:
        logger.warning("%s: 配置的宏为空，跳过插入", note)
        return

    if abs(state.current_z - params.pen_up_z) > FLOAT_EPS:
        lift_cmd = f"G0 Z{params.pen_up_z}"
        logger.debug("%s: 当前 Z=%.3f，先抬笔 -> %s", note, state.current_z, lift_cmd)
        buffer.append(lift_cmd)
        state.current_z = params.pen_up_z
        state.pen_down = False

    buffer.append(f"; ---- {note} 开始 ----")
    for raw in macro_lines:
        line = _format_macro_line(raw, params.macro_context)
        buffer.append(line)
        z_value = _extract_axis(line, "Z")
        if z_value is not None:
            state.current_z = z_value
            state.pen_down = z_value >= params.pen_down_z - FLOAT_EPS
    buffer.append(f"; ---- {note} 结束 ----")

    # 默认宏末尾会回到抬笔，如未回则强制回
    if abs(state.current_z - params.pen_up_z) > FLOAT_EPS:
        buffer.append(f"G0 Z{params.pen_up_z}")
        state.current_z = params.pen_up_z
    state.pen_down = False


class _SafeDict(dict):
    """format_map 使用的字典，缺失键时回显占位字符串方便排查。"""

    def __missing__(self, key: str) -> str:  # pragma: no cover - 容错路径
        logger.warning("宏模板中引用了未知键 %s", key)
        return f"{{{key}}}"


def _format_macro_line(template: str, context: Dict[str, float]) -> str:
    """渲染宏模板行。"""

    safe_context = _SafeDict(context)
    return template.format_map(safe_context)
