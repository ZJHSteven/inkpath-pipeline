# 写字机流水线工具说明

本项目实现一个“写字机流水线”，包含**SVG 字体排版**、**G-code 后处理**与**tkinter GUI**三大模块，可直接为基于 GRBL/ESP32 的写字机提供上游数据。坐标约定：纸张原点位于左上角，X 向右为正，Y 向下为负，Z=0 表示抬笔，Z>0 表示落笔。

## 目录结构

```
plotter_tool/
  config.py      # 负责 config.json 的加载/保存
  svg_font.py    # 模块1：把汉字从 SVG 字体里抠出并按 A4 排版
  gcode_post.py  # 模块2：G-code 补进给/蘸墨/换纸
  cli.py         # CLI 入口，提供 layout 与 post 子命令
  gui.py         # 模块3：本地 tkinter GUI（三个按钮 + 日志框）
config.json       # 可自定义纸张、间距、宏模板等配置
```

## 安装与运行（最小可运行示例）

1. **安装依赖**（推荐使用 uv，自动读取 pyproject 配置）
   ```bash
   uv pip install -e .
   # 或未来从 PyPI 获取：
   uv pip install inkpath-pipeline
   ```
2. **准备字体**：将包含 `<glyph>` 的 SVG 字体文件放到任意路径，例如 `font.svg`。
3. **文本排版**：
   ```bash
   inkplot layout \
     --text "书写流水线" \
     --font-svg /path/to/font.svg \
     --output outputs/sample.svg
   ```
   终端会打印缺字表格，`outputs/sample.svg` 可直接用 Inkscape 打开并导出 G-code。
4. **G-code 后处理**：
   ```bash
   inkplot post \
     --input inputs/raw.nc \
     --output outputs/with-macros.nc
   ```
   程序会自动在第二行补 `G1 F{default_feedrate}`，并按配置插入蘸墨/换纸宏，同时打印“蘸墨/换纸次数”。
5. **GUI 体验**：
   ```bash
   inkplot-gui
   ```
   左侧三个按钮分别触发排版、后处理与配置编辑；右侧滚动日志会记录缺字、宏插入次数等信息。

## 模块说明

### 模块 1：SVG 字体排版（`plotter_tool/svg_font.py`）
- 使用 `xml.etree.ElementTree` 解析 `<glyph unicode="…" d="…">`，建立 Unicode→Path 映射。
- 依据 `config.json` 的 `layout` 和 `page` 参数计算行列坐标；Y 轴在 SVG 中保持负值，并通过 `scale(s, -s)` 翻转，确保与写字机坐标一致。
- 每个字符输出为 `<g id="char-XXX" transform="translate(x, y) scale(s, -s)">`，方便后续肉眼检查。
- 当字体缺字时，自动放置方框占位并在终端/GUI 日志打印缺字表。

### 模块 2：G-code 后处理（`plotter_tool/gcode_post.py`）
- 在首两条指令缺少进给速度 `F` 时，强制在首行后补 `G1 F{default_feedrate}`，避免 GRBL 报错。
- 遍历 G-code 时记录当前 Z、笔状态以及绘制次数：
  - 每满 `insert_every_n_moves` 条真正的绘制指令（`G1` 且笔落下）插一次蘸墨宏；
  - 蘸墨累积到 `insert_every_n_ink` 次，再插一次换纸宏；
  - 插宏前若 Z 不在 `pen_up_z`，会自动补一条抬笔指令，保证拖笔安全。
- 宏模板可以在 `config.json` 的 `macros` 中直接写完整 G-code，并支持 `{ink_x}`、`{pen_up_z}` 之类的占位符，由 `build_macro_context` 自动替换。

### 模块 3：tkinter GUI（`plotter_tool/gui.py`）
- 左栏 3 个按钮：文字排版 / G-code 后处理 / 配置；右栏滚动日志实时输出缺字及宏插入次数。
- 排版界面支持文本输入、多行粘贴、字体/输出路径选择以及方向/间距微调。
- 后处理界面可快速选择输入/输出文件，并临时覆盖抬笔高度、频次、默认进给速度。
- 配置界面将 `config.json` 常用字段做成输入框/下拉框；点击保存后立即写回磁盘，下次 CLI/GUI 自动加载。

## 配置说明（`config.json`）

- `page.width_mm / height_mm`：默认 210×297，对应 A4 竖版；GUI 可自定义。
- `layout`：`cell_size_mm`、`char_spacing_ratio`、`line_spacing_ratio`、`direction`、`font_units_per_em`。
- `plotter`：`pen_up_z`、`pen_down_z`、`safe_z` 对应抬笔/落笔/安全高度。
- `positions`：`ink`、`paper` 的 X/Y，供宏模板引用。
- `macros`：`ink_macro` 与 `paper_macro` 各自是一组完整的 G-code 行，支持格式化占位符。
- `gcode`：`insert_every_n_moves`（蘸墨频次）、`insert_every_n_ink`（换纸频次，设为 0 可跳过）、`default_feedrate`。

> **提示**：`config.py` 会在首次运行自动生成默认配置；若手动编辑格式出错，CLI/GUI 会抛出 `ConfigError` 以提醒修复。

## 开发与测试

- 所有模块以 `plotter_tool` 包方式组织，可在虚拟环境中执行 `python -m plotter_tool.cli --help` 浏览所有选项。
- 建议在提交前运行 `python -m plotter_tool.cli layout --help`、`python -m plotter_tool.cli post --help` 确认参数无误；GUI 可通过 `python - <<'PY'` 的方式导入测试以避免弹窗影响自动化环境。
- 增量开发时记得同步更新 `AGENTS.md`，便于记录每次改动的上下文。
