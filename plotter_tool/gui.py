"""gui.py
==========
该模块提供一个朴素的 tkinter GUI，配合 CLI 完成三大任务：
1. 文字排版 -> SVG；
2. G-code 后处理 -> 新文件；
3. 修改 config.json -> 持久化生效。
所有控件都配有中文说明，帮助首次接触写字机流水线的同学快速上手。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Dict, Iterable

from .config import load_config, save_config
from .svg_font import LayoutParams, SUPPORTED_DIRECTIONS, export_text
from .gcode_post import JobParams, PostParams, build_macro_context, post_process


class PlotterApp(tk.Tk):
    """主窗口，负责构建按钮区与日志区。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("写字机流水线 GUI")
        self.geometry("960x640")
        self.resizable(True, True)
        self.config_data: Dict = load_config()
        self._build_layout()

    # --- 布局 ---------------------------------------------------------
    def _build_layout(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        controls = ttk.Frame(container)
        controls.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(controls, text="功能模块").pack(pady=(0, 8))
        ttk.Button(controls, text="文字排版 SVG", command=self._open_layout_window).pack(fill=tk.X, pady=4)
        ttk.Button(controls, text="G-code 后处理", command=self._open_gcode_window).pack(fill=tk.X, pady=4)
        ttk.Button(controls, text="配置", command=self._open_config_window).pack(fill=tk.X, pady=4)

        log_frame = ttk.Frame(container)
        log_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        ttk.Label(log_frame, text="运行日志").pack(anchor=tk.W)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # --- 日志 ---------------------------------------------------------
    def log(self, message: str) -> None:
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    # --- SVG 排版 -----------------------------------------------------
    def _open_layout_window(self) -> None:
        """构造排版窗口：顶部放文本，中部可滚动参数区，底部固定操作按钮。"""

        window = tk.Toplevel(self)
        window.title("文字排版")
        window.geometry("600x640")  # 初始尺寸足够展示全部控件
        window.minsize(520, 520)  # 允许拉伸，但限制极端缩放
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        layout_cfg = self.config_data["layout"]
        page_cfg = self.config_data["page"]
        layout_paths = self.config_data.get("paths", {}).get("layout", {})

        text_frame = ttk.LabelFrame(window, text="输入文字（可多行）")
        text_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        text_widget = tk.Text(text_frame, height=6, wrap=tk.WORD)
        text_widget.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        font_var = tk.StringVar(value=layout_paths.get("font_svg", ""))
        output_var = tk.StringVar(value=layout_paths.get("output_svg", ""))
        direction_var = tk.StringVar(value=layout_cfg["direction"])

        scrollable_form = _ScrollableFrame(window)
        scrollable_form.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        form = scrollable_form.body  # body 承载真实控件

        path_section = ttk.LabelFrame(form, text="路径配置")
        path_section.pack(fill=tk.X, pady=(0, 8))
        self._add_path_field(
            path_section,
            "字体 SVG",
            font_var,
            filedialog.askopenfilename,
            initial_path=layout_paths.get("font_svg"),
        )
        self._add_path_field(
            path_section,
            "输出 SVG",
            output_var,
            filedialog.asksaveasfilename,
            initial_path=layout_paths.get("output_svg"),
        )

        param_section = ttk.LabelFrame(form, text="排版参数（单位：mm）")
        param_section.pack(fill=tk.X, pady=(0, 8))
        fields: Dict[str, tk.Entry] = {}
        self._add_labeled_entry(param_section, "字格", layout_cfg["cell_size_mm"], 12, fields, "cell")
        self._add_labeled_entry(param_section, "字距系数", layout_cfg["char_spacing_ratio"], 12, fields, "char")
        self._add_labeled_entry(param_section, "行距系数", layout_cfg["line_spacing_ratio"], 12, fields, "line")
        self._add_labeled_entry(param_section, "页面宽", page_cfg["width_mm"], 12, fields, "page_w")
        self._add_labeled_entry(param_section, "页面高", page_cfg["height_mm"], 12, fields, "page_h")
        self._add_labeled_entry(param_section, "units/em", layout_cfg["font_units_per_em"], 12, fields, "units")

        ttk.Label(param_section, text="排版方向").pack(anchor=tk.W, pady=(6, 0))
        ttk.Combobox(
            param_section,
            values=list(SUPPORTED_DIRECTIONS),
            textvariable=direction_var,
            state="readonly",
        ).pack(fill=tk.X)

        def run_layout() -> None:
            text_value = text_widget.get("1.0", tk.END).strip()  # 取出输入内容并去掉多余空白
            if not text_value:
                messagebox.showwarning("缺少文字", "请输入要排版的文字内容。")
                return
            if not font_var.get() or not output_var.get():
                messagebox.showwarning("缺少路径", "请先选择字体 SVG 和输出路径。")
                return
            try:
                params = LayoutParams(  # 组装排版参数
                    text=text_value,
                    font_path=Path(font_var.get()),
                    output_path=Path(output_var.get()),
                    page_width=float(fields["page_w"].get()),
                    page_height=float(fields["page_h"].get()),
                    cell_size=float(fields["cell"].get()),
                    char_spacing_ratio=float(fields["char"].get()),
                    line_spacing_ratio=float(fields["line"].get()),
                    direction=direction_var.get(),
                    font_units_per_em=float(fields["units"].get()),
                )
                result = export_text(params)  # 调用核心模块完成排版
            except Exception as exc:  # pragma: no cover - GUI 环境下不易自动化
                messagebox.showerror("生成失败", str(exc))
                self.log(f"[排版失败] {exc}")
                return
            self.log(f"[排版完成] 输出 -> {result.output_path}")  # 将结果写入日志
            if result.missing_chars:
                self.log(_format_missing_table(result.missing_chars))
            messagebox.showinfo("完成", f"SVG 已生成：{result.output_path}")

        action_bar = ttk.Frame(window)
        action_bar.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 12))
        action_bar.columnconfigure(0, weight=1)
        ttk.Label(action_bar, text="提示：可用鼠标滚轮浏览所有参数").grid(row=0, column=0, sticky="w")
        ttk.Button(action_bar, text="生成 SVG", command=run_layout).grid(row=0, column=1, sticky="e")

    # --- G-code 后处理 -------------------------------------------------
    def _open_gcode_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("G-code 合并后处理")
        window.geometry("640x640")
        window.minsize(580, 520)

        # 为了防止参数面板过长导致按钮区域被挤出可视区，这里将窗口拆成上下两部分：
        # 1) 顶部使用可复用的 _ScrollableFrame 承载所有参数表单，自动提供滚动条；
        # 2) 底部固定一个操作条，始终展示执行按钮与提示，操作时无需再滚动回底部。
        scrollable = _ScrollableFrame(window)
        scrollable.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))
        form = scrollable.body

        post_paths = self.config_data.get("paths", {}).get("post", {})
        plotter = self.config_data["plotter"]
        gcfg = self.config_data["gcode"]
        writing_cfg = gcfg.get("writing", {})
        drawing_cfg = gcfg.get("drawing", {})
        marker_cfg = gcfg.get("marker", {})

        writing_var = tk.StringVar(value=post_paths.get("writing_input", ""))
        drawing_var = tk.StringVar(value=post_paths.get("drawing_input", ""))
        output_var = tk.StringVar(value=post_paths.get("merged_output", ""))

        path_section = ttk.LabelFrame(form, text="G-code 路径")
        path_section.pack(fill=tk.X, padx=8, pady=8)
        self._add_path_field(
            path_section,
            "写字 G-code",
            writing_var,
            filedialog.askopenfilename,
            initial_path=post_paths.get("writing_input"),
        )
        self._add_path_field(
            path_section,
            "绘画 G-code",
            drawing_var,
            filedialog.askopenfilename,
            initial_path=post_paths.get("drawing_input"),
        )
        self._add_path_field(
            path_section,
            "合并输出",
            output_var,
            filedialog.asksaveasfilename,
            initial_path=post_paths.get("merged_output"),
        )

        fields: Dict[str, tk.Entry] = {}

        writing_section = ttk.LabelFrame(form, text="写字蘸墨策略")
        writing_section.pack(fill=tk.X, padx=8, pady=(0, 8))
        writing_mode_var = tk.StringVar(value=writing_cfg.get("ink_mode", "marker"))
        ttk.Label(writing_section, text="蘸墨模式").pack(anchor=tk.W)
        ttk.Combobox(
            writing_section,
            values=["off", "marker", "stroke"],
            textvariable=writing_mode_var,
            state="readonly",
        ).pack(fill=tk.X, pady=(0, 6))
        self._add_labeled_entry(
            writing_section,
            "笔画阈值（stroke 模式）",
            writing_cfg.get("stroke_interval", 40),
            12,
            fields,
            "writing_interval",
        )
        ttk.Label(writing_section, text="标记行（marker 模式整行匹配）").pack(anchor=tk.W)
        marker_var = tk.StringVar(value=marker_cfg.get("token", ""))
        marker_entry = ttk.Entry(writing_section, textvariable=marker_var)
        marker_entry.pack(fill=tk.X, pady=(0, 6))

        drawing_section = ttk.LabelFrame(form, text="绘画蘸墨策略（固定笔画计数）")
        drawing_section.pack(fill=tk.X, padx=8, pady=(0, 8))
        self._add_labeled_entry(
            drawing_section,
            "笔画阈值",
            drawing_cfg.get("stroke_interval", 80),
            12,
            fields,
            "drawing_interval",
        )

        machine_section = ttk.LabelFrame(form, text="机台参数")
        machine_section.pack(fill=tk.X, padx=8, pady=(0, 8))
        self._add_labeled_entry(machine_section, "抬笔 Z", plotter["pen_up_z"], 12, fields, "pen_up")
        self._add_labeled_entry(machine_section, "落笔 Z", plotter["pen_down_z"], 12, fields, "pen_down")
        self._add_labeled_entry(machine_section, "默认 Feed", gcfg["default_feedrate"], 12, fields, "feed")

        def run_post() -> None:
            writing_path = writing_var.get().strip()
            drawing_path = drawing_var.get().strip()
            output_path = output_var.get().strip()
            if not writing_path or not drawing_path or not output_path:
                messagebox.showwarning("缺少路径", "请完善写字/绘画/输出三条路径。")
                return

            def _read_optional_int(key: str) -> int | None:
                raw = fields[key].get().strip()
                return int(raw) if raw else None

            writing_interval = _read_optional_int("writing_interval")
            drawing_interval = _read_optional_int("drawing_interval")
            writing_mode = writing_mode_var.get()
            if writing_mode == "stroke" and not writing_interval:
                messagebox.showwarning("缺少写字阈值", "写字选择 stroke 模式时必须填写笔画阈值。")
                return
            if not drawing_interval:
                messagebox.showwarning("缺少绘画阈值", "请填写绘画阶段的笔画阈值。")
                return

            try:
                params = PostParams(
                    writing=JobParams(
                        name="写字",
                        input_path=Path(writing_path),
                        ink_mode=writing_mode,
                        stroke_interval=writing_interval,
                    ),
                    drawing=JobParams(
                        name="绘画",
                        input_path=Path(drawing_path),
                        ink_mode="stroke",
                        stroke_interval=drawing_interval,
                    ),
                    output_path=Path(output_path),
                    pen_up_z=float(fields["pen_up"].get()),
                    pen_down_z=float(fields["pen_down"].get()),
                    default_feedrate=float(fields["feed"].get()),
                    ink_macro=self.config_data["macros"]["ink_macro"],
                    paper_macro=self.config_data["macros"]["paper_macro"],
                    macro_context=build_macro_context(
                        self.config_data["plotter"],
                        self.config_data.get("positions", {}),
                    ),
                    marker_token=marker_var.get().strip(),
                )
                result = post_process(params)
            except Exception as exc:  # pragma: no cover
                messagebox.showerror("处理失败", str(exc))
                self.log(f"[处理失败] {exc}")
                return
            self.log(
                f"[处理完成] 写字蘸墨 {result.writing_ink_times} 次，绘画蘸墨 {result.drawing_ink_times} 次，"
                f"换纸 {result.paper_times} 次，总行数 {result.total_lines} -> {result.output_path}"
            )
            messagebox.showinfo("完成", f"G-code 已写入：{result.output_path}")

        action_bar = ttk.Frame(window)
        action_bar.pack(fill=tk.X, padx=8, pady=(8, 12))
        action_bar.columnconfigure(0, weight=1)
        ttk.Label(action_bar, text="提示：参数面板可滚动，按钮始终保持可见").grid(row=0, column=0, sticky="w")
        ttk.Button(action_bar, text="执行后处理", command=run_post).grid(row=0, column=1, sticky="e")
    # --- 配置编辑 -----------------------------------------------------
    def _open_config_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("配置管理")
        window.geometry("560x640")
        window.minsize(520, 520)

        scrollable = _ScrollableFrame(window)
        scrollable.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        form = scrollable.body

        entries: Dict[str, tk.Entry] = {}
        paths_cfg = self.config_data.get("paths", {})
        layout_paths = paths_cfg.get("layout", {})
        post_paths = paths_cfg.get("post", {})
        gcfg = self.config_data["gcode"]
        writing_cfg = gcfg.get("writing", {})
        drawing_cfg = gcfg.get("drawing", {})
        marker_cfg = gcfg.get("marker", {})

        def section(title: str) -> ttk.LabelFrame:
            frame = ttk.LabelFrame(form, text=title)
            frame.pack(fill=tk.X, pady=6)
            return frame

        page_frame = section("纸张")
        self._add_labeled_entry(page_frame, "页面宽 (mm)", self.config_data["page"]["width_mm"], 12, entries, "page_w")
        self._add_labeled_entry(page_frame, "页面高 (mm)", self.config_data["page"]["height_mm"], 12, entries, "page_h")

        layout_frame = section("排版")
        self._add_labeled_entry(layout_frame, "字格 (mm)", self.config_data["layout"]["cell_size_mm"], 12, entries, "cell")
        self._add_labeled_entry(layout_frame, "字距系数", self.config_data["layout"]["char_spacing_ratio"], 12, entries, "char")
        self._add_labeled_entry(layout_frame, "行距系数", self.config_data["layout"]["line_spacing_ratio"], 12, entries, "line")
        self._add_labeled_entry(layout_frame, "units/em", self.config_data["layout"]["font_units_per_em"], 12, entries, "units")
        direction_var = tk.StringVar(value=self.config_data["layout"]["direction"])
        ttk.Label(layout_frame, text="排版方向").pack(anchor=tk.W)
        ttk.Combobox(layout_frame, values=list(SUPPORTED_DIRECTIONS), textvariable=direction_var, state="readonly").pack(fill=tk.X)

        layout_path_frame = section("排版默认路径")
        self._add_labeled_entry(layout_path_frame, "字体 SVG", layout_paths.get("font_svg", ""), 32, entries, "layout_font_path")
        self._add_labeled_entry(layout_path_frame, "输出 SVG", layout_paths.get("output_svg", ""), 32, entries, "layout_output_path")

        post_path_frame = section("G-code 默认路径")
        self._add_labeled_entry(post_path_frame, "写字 G-code", post_paths.get("writing_input", ""), 32, entries, "post_writing_path")
        self._add_labeled_entry(post_path_frame, "绘画 G-code", post_paths.get("drawing_input", ""), 32, entries, "post_drawing_path")
        self._add_labeled_entry(post_path_frame, "合并输出", post_paths.get("merged_output", ""), 32, entries, "post_output_path")

        z_frame = section("Z 轴")
        self._add_labeled_entry(z_frame, "抬笔 Z", self.config_data["plotter"]["pen_up_z"], 12, entries, "pen_up")
        self._add_labeled_entry(z_frame, "落笔 Z", self.config_data["plotter"]["pen_down_z"], 12, entries, "pen_down")
        self._add_labeled_entry(z_frame, "安全 Z", self.config_data["plotter"].get("safe_z", 1.0), 12, entries, "safe_z")

        pos_frame = section("蘸墨/换纸坐标")
        positions = self.config_data["positions"]
        self._add_labeled_entry(pos_frame, "蘸墨 X", positions["ink"]["x"], 12, entries, "ink_x")
        self._add_labeled_entry(pos_frame, "蘸墨 Y", positions["ink"]["y"], 12, entries, "ink_y")
        self._add_labeled_entry(pos_frame, "换纸 X", positions["paper"]["x"], 12, entries, "paper_x")
        self._add_labeled_entry(pos_frame, "换纸 Y", positions["paper"]["y"], 12, entries, "paper_y")

        gcode_frame = section("G-code 参数")
        self._add_labeled_entry(gcode_frame, "默认 Feed", gcfg["default_feedrate"], 12, entries, "feed")
        writing_mode_var = tk.StringVar(value=writing_cfg.get("ink_mode", "marker"))
        ttk.Label(gcode_frame, text="写字蘸墨模式").pack(anchor=tk.W)
        ttk.Combobox(gcode_frame, values=["off", "marker", "stroke"], textvariable=writing_mode_var, state="readonly").pack(fill=tk.X)
        self._add_labeled_entry(gcode_frame, "写字笔画阈值", writing_cfg.get("stroke_interval", 40), 12, entries, "cfg_writing_interval")
        self._add_labeled_entry(gcode_frame, "绘画笔画阈值", drawing_cfg.get("stroke_interval", 80), 12, entries, "cfg_drawing_interval")
        ttk.Label(gcode_frame, text="Marker 标记行").pack(anchor=tk.W)
        marker_var = tk.StringVar(value=marker_cfg.get("token", ""))
        marker_entry = ttk.Entry(gcode_frame, textvariable=marker_var)
        marker_entry.pack(fill=tk.X, pady=(0, 6))

        def save_changes() -> None:
            try:
                new_cfg = load_config()
                new_cfg["page"]["width_mm"] = float(entries["page_w"].get())
                new_cfg["page"]["height_mm"] = float(entries["page_h"].get())
                new_cfg["layout"]["cell_size_mm"] = float(entries["cell"].get())
                new_cfg["layout"]["char_spacing_ratio"] = float(entries["char"].get())
                new_cfg["layout"]["line_spacing_ratio"] = float(entries["line"].get())
                new_cfg["layout"]["font_units_per_em"] = float(entries["units"].get())
                new_cfg["layout"]["direction"] = direction_var.get()
                new_cfg.setdefault("paths", {}).setdefault("layout", {})["font_svg"] = entries["layout_font_path"].get().strip()
                new_cfg["paths"]["layout"]["output_svg"] = entries["layout_output_path"].get().strip()
                new_cfg.setdefault("paths", {}).setdefault("post", {})["writing_input"] = entries["post_writing_path"].get().strip()
                new_cfg["paths"]["post"]["drawing_input"] = entries["post_drawing_path"].get().strip()
                new_cfg["paths"]["post"]["merged_output"] = entries["post_output_path"].get().strip()
                new_cfg["plotter"]["pen_up_z"] = float(entries["pen_up"].get())
                new_cfg["plotter"]["pen_down_z"] = float(entries["pen_down"].get())
                new_cfg["plotter"]["safe_z"] = float(entries["safe_z"].get())
                new_cfg["positions"]["ink"]["x"] = float(entries["ink_x"].get())
                new_cfg["positions"]["ink"]["y"] = float(entries["ink_y"].get())
                new_cfg["positions"]["paper"]["x"] = float(entries["paper_x"].get())
                new_cfg["positions"]["paper"]["y"] = float(entries["paper_y"].get())
                new_cfg["gcode"]["default_feedrate"] = float(entries["feed"].get())
                new_cfg["gcode"]["writing"]["ink_mode"] = writing_mode_var.get()
                new_cfg["gcode"]["writing"]["stroke_interval"] = int(entries["cfg_writing_interval"].get())
                new_cfg["gcode"]["drawing"]["stroke_interval"] = int(entries["cfg_drawing_interval"].get())
                new_cfg["gcode"]["marker"]["token"] = marker_var.get().strip()
                save_config(new_cfg)
                self.config_data = new_cfg
            except Exception as exc:  # pragma: no cover
                messagebox.showerror("保存失败", str(exc))
                self.log(f"[配置保存失败] {exc}")
                return
            self.log("[配置已更新] config.json 写入完成")
            messagebox.showinfo("成功", "配置写入成功，重启 GUI 生效。")

        action_bar = ttk.Frame(window)
        action_bar.pack(fill=tk.X, padx=8, pady=(0, 12))
        ttk.Button(action_bar, text="保存配置", command=save_changes).pack(side=tk.RIGHT)
    # --- 通用控件 -----------------------------------------------------
    def _add_path_field(
        self,
        parent: tk.Misc,
        label: str,
        var: tk.StringVar,
        dialog_func,
        initial_path: str | None = None,
    ) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(frame, text=label).pack(anchor=tk.W)
        ttk.Entry(frame, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        if initial_path and not var.get():
            var.set(initial_path)

        def choose() -> None:
            current_hint = var.get().strip() or (initial_path or "")
            dialog_kwargs = self._dialog_defaults(current_hint)
            path = dialog_func(**dialog_kwargs) if dialog_kwargs else dialog_func()
            if path:
                var.set(path)

        ttk.Button(frame, text="浏览", command=choose).pack(side=tk.RIGHT, padx=4)

    def _dialog_defaults(self, path_hint: str) -> Dict[str, str]:
        """根据当前文本/默认值推断文件对话框的初始目录与文件名。"""

        if not path_hint:
            return {}
        candidate = Path(path_hint).expanduser()
        if candidate.is_dir():
            return {"initialdir": str(candidate)}
        return {"initialdir": str(candidate.parent), "initialfile": candidate.name}

    def _add_labeled_entry(
        self,
        parent: tk.Misc,
        label: str,
        value,
        width: int,
        bucket: Dict[str, tk.Entry],
        key: str,
    ) -> None:
        ttk.Label(parent, text=label).pack(anchor=tk.W)
        entry = ttk.Entry(parent, width=width)
        entry.insert(0, str(value))
        entry.pack(fill=tk.X, pady=(0, 6))
        bucket[key] = entry


def _format_missing_table(missing: Iterable[str]) -> str:
    unique = sorted(set(missing))
    lines = ["缺字列表："]
    for idx, char in enumerate(unique, 1):
        safe = char if char.strip() else "(空白)"
        lines.append(f"  {idx:02d}. {safe}")
    return "\n".join(lines)


def launch() -> None:
    app = PlotterApp()
    app.mainloop()


class _ScrollableFrame(ttk.Frame):
    """为表单提供垂直滚动能力，保证按钮不因窗口过小而不可见。"""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")  # 画布承担滚动区域
        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self.body = ttk.Frame(self._canvas)
        self._window_id = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._sync_scrollregion)
        self._canvas.bind("<Configure>", self._sync_width)
        _bind_mousewheel(self.body, self._canvas)

    def _sync_scrollregion(self, event: tk.Event) -> None:
        """根据内部控件尺寸实时更新滚动范围。"""

        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _sync_width(self, event: tk.Event) -> None:
        """保持内部 Frame 宽度与画布一致，避免出现水平滚动条。"""

        self._canvas.itemconfigure(self._window_id, width=event.width)


def _bind_mousewheel(widget: tk.Misc, canvas: tk.Canvas) -> None:
    """统一鼠标滚轮事件，兼容 Windows / macOS / Linux。"""

    def _on_mousewheel(event: tk.Event) -> None:
        delta = event.delta if event.delta else 0
        if delta == 0:
            return
        canvas.yview_scroll(int(-delta / 120), "units")

    widget.bind("<MouseWheel>", _on_mousewheel, add=True)
    widget.bind("<Button-4>", lambda _: canvas.yview_scroll(-1, "units"), add=True)
    widget.bind("<Button-5>", lambda _: canvas.yview_scroll(1, "units"), add=True)
