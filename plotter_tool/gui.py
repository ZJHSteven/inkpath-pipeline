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
from .gcode_post import PostParams, build_macro_context, post_process


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
        window = tk.Toplevel(self)
        window.title("文字排版")
        window.geometry("540x560")

        layout_cfg = self.config_data["layout"]
        page_cfg = self.config_data["page"]

        tk.Label(window, text="输入文字 (支持多行)").pack(anchor=tk.W, padx=8, pady=(8, 0))
        text_widget = tk.Text(window, height=6)
        text_widget.pack(fill=tk.X, padx=8)

        font_var = tk.StringVar()
        output_var = tk.StringVar()
        direction_var = tk.StringVar(value=layout_cfg["direction"])
        self._add_path_field(window, "字体 SVG", font_var, filedialog.askopenfilename)
        self._add_path_field(window, "输出 SVG", output_var, filedialog.asksaveasfilename)

        form = ttk.Frame(window)
        form.pack(fill=tk.X, padx=8, pady=8)
        fields: Dict[str, tk.Entry] = {}
        self._add_labeled_entry(form, "字格 (mm)", layout_cfg["cell_size_mm"], 12, fields, "cell")
        self._add_labeled_entry(form, "字距系数", layout_cfg["char_spacing_ratio"], 12, fields, "char")
        self._add_labeled_entry(form, "行距系数", layout_cfg["line_spacing_ratio"], 12, fields, "line")
        self._add_labeled_entry(form, "页面宽 (mm)", page_cfg["width_mm"], 12, fields, "page_w")
        self._add_labeled_entry(form, "页面高 (mm)", page_cfg["height_mm"], 12, fields, "page_h")
        self._add_labeled_entry(form, "units/em", layout_cfg["font_units_per_em"], 12, fields, "units")

        ttk.Label(form, text="排版方向").pack(anchor=tk.W, pady=(6, 0))
        ttk.Combobox(form, values=list(SUPPORTED_DIRECTIONS), textvariable=direction_var, state="readonly").pack(fill=tk.X)

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

        ttk.Button(window, text="生成 SVG", command=run_layout).pack(pady=12)

    # --- G-code 后处理 -------------------------------------------------
    def _open_gcode_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("G-code 后处理")
        window.geometry("520x440")

        input_var = tk.StringVar()
        output_var = tk.StringVar()
        self._add_path_field(window, "原始 G-code", input_var, filedialog.askopenfilename)
        self._add_path_field(window, "输出 G-code", output_var, filedialog.asksaveasfilename)

        form = ttk.Frame(window)
        form.pack(fill=tk.X, padx=8, pady=8)
        fields: Dict[str, tk.Entry] = {}
        plotter = self.config_data["plotter"]
        gcfg = self.config_data["gcode"]
        self._add_labeled_entry(form, "抬笔 Z", plotter["pen_up_z"], 10, fields, "pen_up")
        self._add_labeled_entry(form, "落笔 Z", plotter["pen_down_z"], 10, fields, "pen_down")
        self._add_labeled_entry(form, "每 N 次蘸墨", gcfg["insert_every_n_moves"], 10, fields, "insert")
        self._add_labeled_entry(form, "每 N 次换纸", gcfg["insert_every_n_ink"], 10, fields, "paper")
        self._add_labeled_entry(form, "默认 Feed", gcfg["default_feedrate"], 10, fields, "feed")

        def run_post() -> None:
            if not input_var.get() or not output_var.get():
                messagebox.showwarning("缺少路径", "请指定输入与输出 G-code。")
                return
            try:
                params = PostParams(  # 组装后处理参数
                    input_path=Path(input_var.get()),
                    output_path=Path(output_var.get()),
                    pen_up_z=float(fields["pen_up"].get()),
                    pen_down_z=float(fields["pen_down"].get()),
                    insert_every_n_moves=int(fields["insert"].get()),
                    insert_every_n_ink=int(fields["paper"].get()),
                    default_feedrate=float(fields["feed"].get()),
                    ink_macro=self.config_data["macros"]["ink_macro"],
                    paper_macro=self.config_data["macros"]["paper_macro"],
                    macro_context=build_macro_context(self.config_data["plotter"], self.config_data.get("positions", {})),
                )
                result = post_process(params)  # 实际执行后处理
            except Exception as exc:  # pragma: no cover
                messagebox.showerror("后处理失败", str(exc))
                self.log(f"[后处理失败] {exc}")
                return
            self.log(
                f"[后处理完成] 蘸墨 {result.ink_times} 次，换纸 {result.paper_times} 次，输出 {result.total_lines} 行 -> {result.output_path}"
            )
            messagebox.showinfo("完成", f"G-code 已写入：{result.output_path}")

        ttk.Button(window, text="执行后处理", command=run_post).pack(pady=12)

    # --- 配置编辑 -----------------------------------------------------
    def _open_config_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("配置管理")
        window.geometry("560x600")

        entries: Dict[str, tk.Entry] = {}

        def section(title: str) -> ttk.LabelFrame:
            frame = ttk.LabelFrame(window, text=title)
            frame.pack(fill=tk.X, padx=8, pady=6)
            return frame

        page_frame = section("纸张")
        self._add_labeled_entry(page_frame, "页面宽 (mm)", self.config_data["page"]["width_mm"], 12, entries, "page_w")
        self._add_labeled_entry(page_frame, "页面高 (mm)", self.config_data["page"]["height_mm"], 12, entries, "page_h")

        layout_frame = section("排版")
        self._add_labeled_entry(layout_frame, "字格 (mm)", self.config_data["layout"]["cell_size_mm"], 12, entries, "cell")
        self._add_labeled_entry(layout_frame, "字距系数", self.config_data["layout"]["char_spacing_ratio"], 12, entries, "char")
        self._add_labeled_entry(layout_frame, "行距系数", self.config_data["layout"]["line_spacing_ratio"], 12, entries, "line")
        direction_var = tk.StringVar(value=self.config_data["layout"]["direction"])
        ttk.Label(layout_frame, text="方向").pack(anchor=tk.W)
        ttk.Combobox(layout_frame, values=list(SUPPORTED_DIRECTIONS), textvariable=direction_var, state="readonly").pack(fill=tk.X)

        z_frame = section("Z 设置")
        self._add_labeled_entry(z_frame, "抬笔 Z", self.config_data["plotter"]["pen_up_z"], 12, entries, "pen_up")
        self._add_labeled_entry(z_frame, "落笔 Z", self.config_data["plotter"]["pen_down_z"], 12, entries, "pen_down")
        self._add_labeled_entry(z_frame, "安全 Z", self.config_data["plotter"].get("safe_z", 1.0), 12, entries, "safe_z")

        pos_frame = section("墨盒/换纸")
        positions = self.config_data["positions"]
        self._add_labeled_entry(pos_frame, "墨盒 X", positions["ink"]["x"], 12, entries, "ink_x")
        self._add_labeled_entry(pos_frame, "墨盒 Y", positions["ink"]["y"], 12, entries, "ink_y")
        self._add_labeled_entry(pos_frame, "换纸 X", positions["paper"]["x"], 12, entries, "paper_x")
        self._add_labeled_entry(pos_frame, "换纸 Y", positions["paper"]["y"], 12, entries, "paper_y")

        gcode_frame = section("G-code")
        gcfg = self.config_data["gcode"]
        self._add_labeled_entry(gcode_frame, "每 N 次蘸墨", gcfg["insert_every_n_moves"], 12, entries, "insert")
        self._add_labeled_entry(gcode_frame, "每 N 次换纸", gcfg["insert_every_n_ink"], 12, entries, "paper")
        self._add_labeled_entry(gcode_frame, "默认 Feed", gcfg["default_feedrate"], 12, entries, "feed")

        def save_changes() -> None:
            try:
                new_cfg = load_config()  # 重新读取以获取最新版本
                new_cfg["page"]["width_mm"] = float(entries["page_w"].get())  # 更新纸张宽度
                new_cfg["page"]["height_mm"] = float(entries["page_h"].get())  # 更新纸张高度
                new_cfg["layout"]["cell_size_mm"] = float(entries["cell"].get())  # 更新字格
                new_cfg["layout"]["char_spacing_ratio"] = float(entries["char"].get())  # 更新字距
                new_cfg["layout"]["line_spacing_ratio"] = float(entries["line"].get())  # 更新行距
                new_cfg["layout"]["direction"] = direction_var.get()  # 更新方向
                new_cfg["plotter"]["pen_up_z"] = float(entries["pen_up"].get())  # 更新抬笔
                new_cfg["plotter"]["pen_down_z"] = float(entries["pen_down"].get())  # 更新落笔
                new_cfg["plotter"]["safe_z"] = float(entries["safe_z"].get())  # 更新安全高度
                new_cfg["positions"]["ink"]["x"] = float(entries["ink_x"].get())  # 更新墨盒 X
                new_cfg["positions"]["ink"]["y"] = float(entries["ink_y"].get())  # 更新墨盒 Y
                new_cfg["positions"]["paper"]["x"] = float(entries["paper_x"].get())  # 更新换纸 X
                new_cfg["positions"]["paper"]["y"] = float(entries["paper_y"].get())  # 更新换纸 Y
                new_cfg["gcode"]["insert_every_n_moves"] = int(entries["insert"].get())  # 更新蘸墨频次
                new_cfg["gcode"]["insert_every_n_ink"] = int(entries["paper"].get())  # 更新换纸频次
                new_cfg["gcode"]["default_feedrate"] = float(entries["feed"].get())  # 更新默认进给
                save_config(new_cfg)
                self.config_data = new_cfg
            except Exception as exc:  # pragma: no cover
                messagebox.showerror("保存失败", str(exc))
                self.log(f"[配置保存失败] {exc}")
                return
            self.log("[配置已保存] config.json 更新完毕")
            messagebox.showinfo("成功", "配置写入完成，下次打开 GUI 会自动加载。")

        ttk.Button(window, text="保存配置", command=save_changes).pack(pady=12)

    # --- 通用控件 -----------------------------------------------------
    def _add_path_field(self, parent: tk.Misc, label: str, var: tk.StringVar, dialog_func) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(frame, text=label).pack(anchor=tk.W)
        ttk.Entry(frame, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        def choose() -> None:
            path = dialog_func()
            if path:
                var.set(path)

        ttk.Button(frame, text="浏览", command=choose).pack(side=tk.RIGHT, padx=4)

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
