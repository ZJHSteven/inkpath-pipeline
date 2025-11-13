"""gcode_post.py
=================
该模块负责对两个独立的 G-code 进行有序合并：
1. 写字 G-code -> 可选择三种蘸墨策略（关闭/按特殊标记/按笔画计数）；
2. 自动插入一次换纸宏，确保纸张切换在抬笔高度完成；
3. 绘画 G-code -> 固定按笔画计数蘸墨；
最终输出单一 G-code 文件，供 GRBL 直接运行。

实现约定：
- 所有磁盘 I/O 均集中在本模块，调用方只需传入路径与配置即可；
- 宏模板允许通过 format_map 使用自定义坐标占位符；
- 错误与边界检查拆分为独立函数，便于 GUI/CLI 统一捕获。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal
import logging
import re

logger = logging.getLogger(__name__)

AXIS_PATTERN = re.compile(r"([XYZF])\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
MOTION_PREFIXES = ("G1", "G01")
COMMENT_PREFIXES = (";", "(")
FLOAT_EPS = 1e-4

InkMode = Literal["off", "marker", "stroke"]
INK_MODE_SET = {"off", "marker", "stroke"}


class GcodePostError(RuntimeError):
    """后处理失败时的统一异常，方便主流程捕获友好提示。"""


@dataclass(frozen=True)
class JobParams:
    """描述单个 G-code 作业的输入与蘸墨策略。"""

    name: str
    input_path: Path
    ink_mode: InkMode
    stroke_interval: int | None = None


@dataclass(frozen=True)
class PostParams:
    """承载后处理所需的全部外部参数。"""

    writing: JobParams
    drawing: JobParams
    output_path: Path
    pen_up_z: float
    pen_down_z: float
    default_feedrate: float
    ink_macro: List[str]
    paper_macro: List[str]
    macro_context: Dict[str, float]
    marker_token: str


@dataclass(frozen=True)
class PostResult:
    """统计信息，帮助上层 UI 显示蘸墨与换纸次数。"""

    writing_ink_times: int
    drawing_ink_times: int
    paper_times: int
    total_lines: int
    output_path: Path


@dataclass
class _State:
    """运行时状态：仅跟踪 Z 轴与笔状态，保持逻辑清晰。"""

    current_z: float
    pen_down: bool


def post_process(params: PostParams) -> PostResult:
    """串联写字/换纸/绘画三个阶段并输出合并后的 G-code。"""

    _validate_params(params)
    buffer: List[str] = []
    state = _State(current_z=params.pen_up_z, pen_down=False)

    writing_ink = _process_job(params.writing, params, state, buffer)
    paper_times = _insert_paper_change(buffer, params, state)
    drawing_ink = _process_job(params.drawing, params, state, buffer)

    params.output_path.write_text("\n".join(buffer) + "\n", encoding="utf-8")
    logger.info("G-code 已写入 %s", params.output_path)
    return PostResult(
        writing_ink_times=writing_ink,
        drawing_ink_times=drawing_ink,
        paper_times=paper_times,
        total_lines=len(buffer),
        output_path=params.output_path,
    )


def _process_job(job: JobParams, params: PostParams, state: _State, buffer: List[str]) -> int:
    """读取单个作业文件并依据策略插入蘸墨宏。"""

    lines = _read_gcode_lines(job, params.default_feedrate)
    ink_counter = 0
    stroke_counter = 0
    previous_pen_down = state.pen_down

    buffer.append(f"; === {job.name} 开始 ===")
    for raw_line in lines:
        stripped = raw_line.strip()

        if job.ink_mode == "marker" and _is_marker_line(stripped, params.marker_token):
            ink_counter += 1
            buffer.append(f"; {job.name} 手动蘸墨 #{ink_counter}")
            _inject_macro(
                buffer,
                params.ink_macro,
                params,
                state,
                note=f"{job.name} 手动蘸墨 #{ink_counter}",
            )
            stroke_counter = 0
            previous_pen_down = state.pen_down
            continue

        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            buffer.append(raw_line)
            continue

        cmd = stripped.split()[0].upper()
        z_value = _extract_axis(stripped, "Z")
        
        if z_value is not None:
            new_pen_down = z_value >= params.pen_down_z - FLOAT_EPS
            
            # 检测落笔→抬笔转换（笔画结束）
            if previous_pen_down and not new_pen_down:
                stroke_counter += 1
                
                # 先输出抬笔指令
                buffer.append(raw_line)
                
                # 笔画结束后检查是否需要蘸墨
                if job.ink_mode == "stroke" and job.stroke_interval:
                    if stroke_counter > 0 and stroke_counter % job.stroke_interval == 0:
                        ink_counter += 1
                        _inject_macro(
                            buffer,
                            params.ink_macro,
                            params,
                            state,
                            note=f"{job.name} 自动蘸墨 #{ink_counter}",
                        )
                        stroke_counter = 0
                
                # 更新状态并继续下一行
                state.current_z = z_value
                state.pen_down = new_pen_down
                previous_pen_down = new_pen_down
                continue
            
            state.current_z = z_value
            state.pen_down = new_pen_down
            previous_pen_down = new_pen_down

        buffer.append(raw_line)

    buffer.append(f"; === {job.name} 结束 ===")
    buffer.append("")
    return ink_counter


def _insert_paper_change(buffer: List[str], params: PostParams, state: _State) -> int:
    """在两个作业之间插入一次换纸宏。"""

    if not params.paper_macro:
        logger.warning("配置未提供换纸宏，跳过自动换纸")
        return 0
    buffer.append("; === 换纸 ===")
    _inject_macro(buffer, params.paper_macro, params, state, note="换纸 #1")
    buffer.append("")
    return 1


def _read_gcode_lines(job: JobParams, default_feed: float) -> List[str]:
    """读取 G-code，若缺乏进给速度则自动补上一条。"""

    if not job.input_path.exists():
        raise FileNotFoundError(f"找不到 {job.name} G-code：{job.input_path}")
    lines = job.input_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise GcodePostError(f"{job.name} G-code 为空：{job.input_path}")
    return _ensure_feedrate(lines, default_feed)


def _validate_params(params: PostParams) -> None:
    """集中处理所有基础合法性检查。"""

    if params.pen_down_z <= params.pen_up_z:
        raise ValueError("pen_down_z 必须大于 pen_up_z")

    marker = params.marker_token.strip()
    for job in (params.writing, params.drawing):
        _validate_job(job, marker)

    params.output_path.parent.mkdir(parents=True, exist_ok=True)


def _validate_job(job: JobParams, marker: str) -> None:
    """校验单个作业配置，确保模式与参数匹配。"""

    if not job.input_path.exists():
        raise FileNotFoundError(f"{job.name} G-code 不存在：{job.input_path}")
    if job.ink_mode not in INK_MODE_SET:
        raise ValueError(f"{job.name} ink_mode 不受支持：{job.ink_mode}")
    if job.ink_mode == "stroke" and (job.stroke_interval or 0) <= 0:
        raise ValueError(f"{job.name} 需要正整数 stroke_interval")
    if job.ink_mode == "marker" and not marker:
        raise ValueError(f"{job.name} 选择 marker 模式但 marker_token 为空")


def _is_marker_line(line: str, marker_token: str) -> bool:
    """判断当前行是否为用户手动插入的蘸墨标记。"""

    token = marker_token.strip()
    return bool(token) and line == token


def _ensure_feedrate(lines: List[str], default_feed: float) -> List[str]:
    """检查前两条有效指令是否包含 F，缺失则自动补齐。"""

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

    upper = line.upper()
    return "X" in upper or "Y" in upper


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
    """把宏指令插入输出缓冲，并维护抬笔状态。"""

    if not macro_lines:
        logger.warning("%s: 配置的宏为空，跳过插入", note)
        return

    buffer.append(f"; ---- {note} 开始 ----")
    for raw in macro_lines:
        line = _format_macro_line(raw, params.macro_context)
        buffer.append(line)
        z_value = _extract_axis(line, "Z")
        if z_value is not None:
            state.current_z = z_value
            state.pen_down = z_value >= params.pen_down_z - FLOAT_EPS
    buffer.append(f"; ---- {note} 结束 ----")

    # 宏执行完成后确保状态为抬笔
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


def build_macro_context(
    plotter_cfg: Dict[str, Any],
    positions_cfg: Dict[str, Dict[str, Any]],
) -> Dict[str, float]:
    """将配置文件中的坐标/Z 值抽取出来，供宏模板格式化使用。"""

    ink = positions_cfg.get("ink", {})
    paper = positions_cfg.get("paper", {})
    return {
        "pen_up_z": float(plotter_cfg.get("pen_up_z", 0.0)),
        "pen_down_z": float(plotter_cfg.get("pen_down_z", 0.0)),
        "safe_z": float(plotter_cfg.get("safe_z", plotter_cfg.get("pen_up_z", 0.0))),
        "ink_x": float(ink.get("x", 0.0)),
        "ink_y": float(ink.get("y", 0.0)),
        "paper_x": float(paper.get("x", 0.0)),
        "paper_y": float(paper.get("y", 0.0)),
    }
