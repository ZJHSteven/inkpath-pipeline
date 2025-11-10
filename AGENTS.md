# AGENTS 变更日志

- 2025-11-08：初始化 plotter_tool 包框架，新增配置模块 config.py、默认 config.json，并在 pyproject.toml 中注册 CLI 入口。
- 2025-11-08：实现 svg_font 排版逻辑与 layout 子命令，支持缺字表格输出。
- 2025-11-08：完成 gcode_post 模块与 post 子命令，支持自动补速、蘸墨/换纸统计。
- 2025-11-08：新增 tkinter GUI，整合排版/G-code/配置操作并完善注释。
- 2025-11-08：补写 README，汇总安装步骤、CLI/GUI 用法与配置说明。
- 2025-11-09：切换 uv/脚本命名，提供 inkplot、inkplot-gui 入口并同步 README。
- 2025-11-09：重构 GUI 文字排版窗口，新增可滚动表单和固定操作条，确保按钮始终可见。
- 2025-11-10：修正 svg_font 坐标系为第四象限并绘制纸框辅助线
- 2025-11-10：恢复 svg_font 屏幕坐标方向和 SVG 纸框 viewBox，避免多重坐标翻转
- 2025-11-10：进一步固定 svg 字符基线偏移，确保第一象限坐标同时保持字形正向
