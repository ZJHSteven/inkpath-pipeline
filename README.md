# 写字机流水线工具说明

本项目实现一个面向 GRBL/ESP32 写字机的全流程工具链，囊括 **SVG 字体排版**、**G-code 自动后处理** 与 **tkinter GUI 交互**。所有坐标统一采用“纸张左上角为原点、X 向右为正、Y 向下为负、Z=0 表示抬笔、Z>0 表示落笔”的约定，方便与常见绘图仪保持一致。

典型使用场景：一位同学负责“画画/背景” G-code，另一位同学负责排版文字，随后由 post 模块把两份 G-code 自动穿插蘸墨与换纸宏，使得两人协作时蘸墨节奏、纸张更新与笔速配置保持一致。

## 推荐操作顺序（双人团队也适用）

1. **初始化环境**：克隆仓库后执行 `uv pip install -e .`（或 `uv pip install inkpath-pipeline`）即可安装 CLI/GUI 入口 `inkplot` 与 `inkplot-gui`。
2. **准备字体与纸张配置**：将包含 `<glyph>` 描述的 SVG 字体放到固定目录（如 `assets/font.svg`），在 `config.json` 中确认 `page` 段是否匹配 A4 或自定义画幅。
3. **排版文字**：使用 CLI `inkplot layout --text ... --font-svg font.svg --output outputs/layout.svg` 或 GUI“文字排版”窗口，把文本转成附带纸框的 SVG，方便后续校对。
4. **人工校对**：在 Inkscape 等软件中检查字符位置、缺字方框与纸框尺寸，必要时微调 `cell_size_mm`、`line_spacing_ratio` 等参数后重新导出 SVG。
5. **G-code 后处理**：将原始 G-code 输入 `inkplot post`。程序会在第二行补齐缺省进给速度 `G1 F{default_feedrate}`，并按照计数自动插入蘸墨（`ink_macro`）与换纸（`paper_macro`）宏。
6. **上机执行/协作合成**：若存在“画画 G-code + 写字 G-code”两段流程，可各自后处理，再用 `copy /b draw_post.nc+write_post.nc output.nc` 等命令串联为单文件，换纸宏将出现在两段之间，而不会干扰写字时的笔画节奏。

## config.json 参数对照表

`config.json` 是 CLI、GUI 与 post 模块共享的单一数据源。各字段含义如下，可按需微调：

### page —— 纸张设定

- `width_mm`：纸张宽度，默认 210mm（A4 竖版）。若改用宣纸，可填写实测宽度。
- `height_mm`：纸张高度，默认 297mm。SVG 纸框和 GUI 预览都会引用该值。

### layout —— 字格与排版

- `cell_size_mm`：单个字格边长（默认 80mm），直接决定字符缩放比例。
- `char_spacing_ratio`：字距系数，表示在字格基础上额外留出的水平间隙占比（默认 0.1）。
- `line_spacing_ratio`：行/列距系数，控制垂直方向空隙占比（默认 0.2；竖排时作用等同“列距”）。
- `direction`：`horizontal` 横排或 `vertical` 竖排。
- `font_units_per_em`：字体内部 units-per-em（默认 1000），用于把毫米尺度换算到 SVG 字体坐标。

### plotter —— Z 轴与安全高度

- `pen_up_z`：抬笔高度（默认 0）。宏执行前若当前 Z ≠ `pen_up_z`，post 会自动补 `G0 Z{pen_up_z}` 确保不拖墨。
- `pen_down_z`：落笔高度（默认 8）。只有当当前 Z ≥ `pen_down_z` 时才累计“绘制次数”，防止空走也触发蘸墨。
- `safe_z`：安全抬笔高度（默认 1）。宏模板可引用 `{safe_z}` 在纸面上方短距离移动。

### positions —— 蘸墨/换纸坐标

- `ink.x / ink.y`：蘸墨坐标，常设在笔架附近。宏模板中可直接写 `{ink_x}`、`{ink_y}`。
- `paper.x / paper.y`：换纸时移动到的安全位置（比如纸面角落或完全离纸的位置）。

### macros —— 宏模板

- `ink_macro`：蘸墨流程的完整指令序列（示例：抬笔 → 走到 `{ink_x},{ink_y}` → 落笔慢速蘸墨 → `G4` 等待 → 抬笔）。
- `paper_macro`：换纸流程（常见做法：抬笔 → 移到 `{paper_x},{paper_y}` → `G4` 等待人工换纸 → 回到安全高度）。
- 可用占位符：`{pen_up_z}`、`{pen_down_z}`、`{safe_z}`、`{ink_x}`、`{ink_y}`、`{paper_x}`、`{paper_y}`。占位符缺失时会原样回显 `{key}` 以提示补全。

### gcode —— 后处理策略

- `insert_every_n_moves`：累计多少条“真实绘制指令”触发一次蘸墨。真实绘制指令指 `G1/G01` 且包含 X/Y，同步满足“笔已落下”条件。默认 80。
- `insert_every_n_ink`：每蘸墨多少次后换纸。默认 5，设为 0 则完全跳过换纸宏。
- `default_feedrate`：若开头两条有效指令都缺少 `F`，post 会在首条指令后插入 `G1 F{default_feedrate}`（默认 1000mm/min），避免 GRBL 报错。

## 蘸墨 / 换纸运行机制与 n 的含义

post 模块内部维护两个核心计数器：

- `move_counter`：遇到满足“G1/G01 + 含 X/Y + 当前 Z ≥ pen_down_z”的指令才 +1。计数达到 `insert_every_n_moves` 时立即插入 `ink_macro`，随后 `move_counter` 清零。
- `ink_insertions`：每次成功插入蘸墨宏后 +1。若 `insert_every_n_ink > 0` 且 `ink_insertions % insert_every_n_ink == 0`，则紧接着插入 `paper_macro`。

触发流程示例（默认 `insert_every_n_moves=80`、`insert_every_n_ink=5`）：

| 条件 | 自动插入 | 说明 |
| ---- | -------- | ---- |
| 写满 80 次真实笔画 | `ink_macro` | 计数只看真正落笔的 G1/G01，不会因为注释或抬笔移动而误触发。 |
| 每累计 5 次蘸墨 | `paper_macro` | 换纸宏位于两次蘸墨之间，方便人工在“画画/写字”两段之间更换纸张。 |
| 插入宏前 Z ≠ `pen_up_z` | `G0 Z{pen_up_z}` | 自动抬笔，防止宏内的 XY 运动刮纸。宏结束后若未回到 `pen_up_z`，post 会补一次抬笔。 |

若需要确保“画画结束 → 换纸 → 写字开始”的节奏，可采用以下策略：

- 给“画画 G-code”单独执行一次 `inkplot post --insert-n <画画笔画数> --paper-every 1`，这样文件尾部一定触发一次换纸。
- “写字 G-code”可继续使用默认 `insert_every_n_moves`/`insert_every_n_ink`，或将 `--paper-every 0` 关闭换纸，仅保留蘸墨。
- 将两份 post 后的文件使用 `copy /b`（Windows）或 `cat`（类 Unix）串联，即可在两段之间自然出现换纸宏。

当 `insert_every_n_moves` 取值大于文件内真实笔画数时，不会触发蘸墨；反之若取值过小，宏会频繁插入影响效率。经验公式：`insert_every_n_moves = 行数 × 每行字数 × 每字落笔次数 (通常≈1)` 可用来估算写字流程需要的阈值。

## 双人协作 / 多份 G-code 合并示例

下面展示一个“画画 + 写字”串联示例，假设两份原始文件分别为 `inputs/draw_raw.nc` 与 `inputs/write_raw.nc`：

```bash
inkplot post --input inputs/draw_raw.nc  --output temp/draw_post.nc  --insert-n 200 --paper-every 1
inkplot post --input inputs/write_raw.nc --output temp/write_post.nc --insert-n 80  --paper-every 0
copy /b temp\\draw_post.nc+temp\\write_post.nc outputs\\page01.nc
```

- 第一行：画画阶段大约有 200 条真实笔画，所以 `--insert-n 200` 可保证整段只蘸墨一次，并在结尾触发换纸（`--paper-every 1`）。
- 第二行：写字阶段仍按默认 80 次一蘸墨，但关闭换纸（`--paper-every 0`），避免途中误换纸。
- 第三行：将两份后处理结果串联。生成的 `page01.nc` 中，“画画”段末尾会看到 `; ---- 换纸 #1 ----`，紧接着是写字段落。

## CLI 与 GUI 快速体验

1. **安装依赖**（推荐 uv）：
   ```bash
   uv pip install -e .
   # 或直接安装已发布包
   uv pip install inkpath-pipeline
   ```
2. **排版示例**：
   ```bash
   inkplot layout \
     --text "书写流水线演示" \
     --font-svg assets/font.svg \
     --output outputs/sample.svg
   ```
   终端会输出现有字体中找不到的字符列表（若有），`outputs/sample.svg` 自带纸框，可直接在 Inkscape 中导出 G-code。
3. **G-code 后处理示例**：
   ```bash
   inkplot post \
     --input inputs/raw.nc \
     --output outputs/with-macros.nc \
     --insert-n 80 \
     --paper-every 5
   ```
   程序会打印“已插入蘸墨 X 次，换纸 Y 次”，并保证输出文件的第二行补齐 `G1 F{default_feedrate}`。
4. **GUI 体验**：
   ```bash
   inkplot-gui
   ```
   - 左侧三个按钮：文字排版 / G-code 后处理 / 配置。
   - 右侧滚动日志输出缺字表、宏统计、报错等信息。
   - GUI 所有表单字段均对应 `config.json`，修改后立即落盘。

## GUI 常用路径设置建议

- **固定目录 + 文件对话框记忆**：tkinter 的 `filedialog` 会记住最近访问的文件夹。把字体放在 `assets/font.svg`、把输出目录固定为 `outputs/`，首次选择后后续对话框会自动跳回该目录，只需点击文件即可。
- **Windows 快速访问**：在资源管理器中右击字体文件夹与输出文件夹，选择“固定到快速访问”，即可在 GUI 文件对话框左侧快速定位。
- **脚本化默认路径**：若希望“一键生成”而不必在 GUI 中多次点击，可创建 PowerShell 脚本并调用 CLI：

  ```powershell
  # scripts/layout_default.ps1
  param(
      [string]$Text = "书写流水线 demo"
  )
  $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
  inkplot layout `
    --text $Text `
    --font-svg "D:\Fonts\inkfont.svg" `
    --output "D:\Plots\$timestamp.svg"
  ```

  ```powershell
  # scripts/post_default.ps1
  param(
      [string]$Input = "D:\Plots\sample_raw.nc"
  )
  $name = [System.IO.Path]::GetFileNameWithoutExtension($Input)
  inkplot post `
    --input $Input `
    --output "D:\Plots\${name}_post.nc" `
    --insert-n 80 `
    --paper-every 5
  ```

  将脚本固定到任务栏或创建桌面快捷方式，即可在既定目录下快速生成/后处理，无需每次手动填写路径。

## 模块说明（深入阅读时再查）

- **`plotter_tool/svg_font.py`**：负责读取 SVG 字体、计算行列坐标并生成带纸框的 SVG。通过 `scale(s, -s)` 将数学坐标翻回屏幕坐标，缺字时输出方框占位并记录日志。
- **`plotter_tool/gcode_post.py`**：核心后处理逻辑。采用状态机追踪当前 Z、笔状态与绘制计数，自动补 `G1 F...`、蘸墨/换纸宏，并在插入宏前后确保抬笔。
- **`plotter_tool/gui.py`**：tkinter GUI，提供排版、后处理、配置三大窗口。排版界面带滚动表单，后处理界面可覆盖阈值，配置界面可直接编辑 `config.json` 字段。
- **`plotter_tool/config.py`**：集中处理配置文件加载/保存/默认模板与深度合并，CLI 与 GUI 复用同一套 API，出现格式错误时抛出 `ConfigError`。
- **`plotter_tool/cli.py`**：argparse 命令行入口，提供 `layout` 与 `post` 子命令，所有参数均可覆盖配置默认值。

## 开发与测试

- 查看所有 CLI 参数：`python -m plotter_tool.cli --help`、`python -m plotter_tool.cli layout --help`、`python -m plotter_tool.cli post --help`。
- GUI 开发调试：`python -m plotter_tool.gui`，或在 Python REPL 中 `from plotter_tool.gui import PlotterApp; PlotterApp().mainloop()`。
- 修改配置/代码后请同步更新 `AGENTS.md` 记录上下文，并在提交前自查：构建通过、文档同步、无多余文件。
- 推荐在实际写字前先用少量字符测试 layout/post 参数，确认蘸墨/换纸节奏符合预期，再进行整页作业。
