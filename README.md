# 写字流水线操作说明

本项目面向基于 GRBL/ESP32 的写字机，提供 **SVG 排版** → **G-code 合并后处理** → **tkinter GUI** 的一体化流程。所有坐标以纸面左上角为原点，X 向右、Y 向下，Z=0 表示抬笔，Z>0 表示落笔，方便与常见的 svg/gcode 习惯保持一致。

## 推荐工作流

1. **安装依赖**：执行 `uv pip install -e .` 或 `uv pip install inkpath-pipeline`，即可获得 CLI `inkplot` 与 GUI `inkplot-gui`。
2. **准备字体/配置**：将 `<glyph>` 样式的字体 SVG 放到固定目录（默认 `assets/fonts/ink_font.svg`），检查 `config.json` 的纸张/格子参数是否符合当前用纸。
3. **文字排版**：运行 `inkplot layout --text ...`（或 GUI 的“文字排版”页面），生成整页 SVG，缺字会在终端/GUI 的日志表格中列出。
4. **人工校验**：用 Inkscape/浏览器开 SVG，确认字符位置、格子尺寸、是否需要调整 `cell_size_mm` 或 `line_spacing_ratio`。
5. **G-code 合并**：分别准备好“写字 G-code”与“绘画/背景 G-code”，然后执行 `inkplot post`。程序会：① 补齐缺省的进给速度；② 对“写字”和“绘画”分阶段插入蘸墨宏；③ 在两段 G-code 中间强制执行一次换纸宏。
6. **实机运行**：将 `post` 生成的合并 G-code 喂给写字机，按照日志提示在换纸宏后手动更换纸张即可。

> 小贴士：layout/post 均支持从 `config.json` 读取默认路径，因此常见命令可以不显式写 `--font-svg/--output/--writing-input` 等参数。

## CLI 最小示例

```bash
# 布局到默认路径（config.paths.layout.output_svg）
inkplot layout --text "静夜思"

# 合并写字 + 绘画两段 G-code，并指定写字使用 marker 模式
inkplot post \ 
  --writing-input artifacts/gcode/text.nc \ 
  --drawing-input artifacts/gcode/drawing.nc \ 
  --output artifacts/gcode/combined.nc \ 
  --writing-mode marker \ 
  --marker-token ";#AUTO_INK#"
```

当写字阶段选择 `marker` 模式时，只需在写字 G-code 中插入一行完全等于 `;#AUTO_INK#` 的注释，即可在该位置触发一次蘸墨。

## `config.json` 字段说明

`config.json` 是 CLI 与 GUI 共用的配置源，建议用 GUI 的“配置”页面维护，或直接编辑 JSON 文件。

### page —— 纸张尺寸
- `width_mm` / `height_mm`：纸张宽高，默认 A4（210 × 297mm）。

### layout —— 字格排版
- `cell_size_mm`：单个字格边长。
- `char_spacing_ratio`：字距系数，0~1。
- `line_spacing_ratio`：行距系数，0~1。
- `direction`：`horizontal` / `vertical`。
- `font_units_per_em`：SVG 字体的 units-per-em，一般为 1000。

### plotter —— Z 轴与安全高度
- `pen_up_z`：抬笔高度。
- `pen_down_z`：落笔高度，只在当前 Z ≥ 该值时才累计笔画次数。
- `safe_z`：安全高度，宏模板可引用 `{safe_z}`。

### positions —— 蘸墨/换纸坐标
- `ink.x / ink.y`：蘸墨点坐标，可在宏中通过 `{ink_x}`, `{ink_y}` 使用。
- `paper.x / paper.y`：换纸时移动到的安全位置。

### macros —— 宏模板
- `ink_macro`：完整的蘸墨流程指令列表。
- `paper_macro`：换纸流程指令列表。

### paths —— 常用文件默认路径
```json
"paths": {
  "layout": {
    "font_svg": "assets/fonts/ink_font.svg",
    "output_svg": "artifacts/layout.svg"
  },
  "post": {
    "writing_input": "artifacts/gcode/writing.nc",
    "drawing_input": "artifacts/gcode/drawing.nc",
    "merged_output": "artifacts/gcode/merged.nc"
  }
}
```
GUI 会自动把文件对话框定位到这些路径；CLI 未显式传参时也会使用它们。

### gcode —— 蘸墨/换纸策略
- `default_feedrate`：当输入 G-code 前两条有效指令缺少 `F` 时，自动插入 `G1 F{default_feedrate}`。
- `writing`：写字阶段配置。
  - `ink_mode`：`off` / `marker` / `stroke`（详见后文）。
  - `stroke_interval`：当 `ink_mode=stroke` 时，每多少条真实笔画自动蘸墨一次。
- `drawing`：绘画阶段配置。
  - `stroke_interval`：固定采用笔画计数策略。
- `marker.token`：写字阶段 `marker` 模式使用的整行标记，默认 `;#AUTO_INK#`。

## 蘸墨 / 换纸运行机制

1. **写字阶段**：根据 `writing.ink_mode` 选择策略：
   | 模式 | 行为 | 典型场景 |
   | --- | --- | --- |
   | `off` | 不插入任何蘸墨宏，方便手动控制 | 毛笔已自带足够墨量或人工介入 |
   | `marker` | 当 G-code 出现与 `marker.token` 完全一致的整行文本时立即插入蘸墨宏，并在日志记录“手动蘸墨 #N” | 结合预览工具，按字符边界手工插入注记 |
   | `stroke` | 每累计 `stroke_interval` 条真实绘制指令（`G1/G01` 且包含 X/Y 且当前为落笔状态）后自动蘸墨 | 简笔画或需要固定节奏的楷书 |
2. **换纸阶段**：写字段结束后必定执行一次 `paper_macro`，确保可以把“写字纸”换成“绘画纸”。如果宏列表为空，则跳过并记录警告。
3. **绘画阶段**：永远按 `drawing.stroke_interval` 以笔画计数插入蘸墨宏。

所有宏都会在抬笔状态执行：若当前 Z 不等于 `pen_up_z`，程序会主动插入 `G0 Z{pen_up_z}`，宏结束后也会自动回到抬笔高度，保证不会拖笔。

## GUI 说明

- **文字排版**：字体/输出路径自动填充为 `config.paths.layout`，点击“浏览”时会直接定位到这些目录。
- **G-code 后处理**：新增“写字 G-code / 绘画 G-code / 合并输出”三条路径输入，提供写字模式下拉框、笔画阈值、marker 标记行输入框，并展示当前的机台 Z/Feed 参数。
- **配置**：可以编辑 `page/layout/paths/plotter/positions/gcode` 全部字段，写字模式同样使用下拉框，避免手动拼写错误。点击“保存配置”后会立即写回 `config.json`。

## 自定义脚本示例

```powershell
# scripts/post_default.ps1
param(
    [string]$Writing = "D:\\Plots\\writing.nc",
    [string]$Drawing = "D:\\Plots\\drawing.nc"
)
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
inkplot post `
  --writing-input $Writing `
  --drawing-input $Drawing `
  --output "D:\\Plots\\${timestamp}_combined.nc" `
  --writing-mode marker `
  --marker-token ";#AUTO_INK#"
```

## 模块概览
- **`plotter_tool/svg_font.py`**：解析字体 SVG、生成页面布局、输出缺字表格。
- **`plotter_tool/gcode_post.py`**：合并写字/换纸/绘画三阶段，支持 marker/笔画/关闭 三种蘸墨策略，并自动补齐 `G1 F...`。
- **`plotter_tool/gui.py`**：tkinter GUI，包含排版、后处理、配置管理三个子窗口。
- **`plotter_tool/config.py`**：集中处理 `config.json` 的加载、默认值合并与保存。
- **`plotter_tool/cli.py`**：基于 argparse 的 CLI，`layout`/`post` 命令与 GUI 共用实现代码。

## 常用命令速查
- 查看帮助：`python -m plotter_tool.cli post --help`
- 仅布局：`inkplot layout --text-file texts/poem.txt`
- 只改写字模式：`inkplot post --writing-mode off`
- 指定 marker token：`inkplot post --marker-token ";#MY_MARKER#"`
- GUI：`python -m plotter_tool.gui`
