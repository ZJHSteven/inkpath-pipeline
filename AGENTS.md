# AGENTS 变更日志

- 2025-11-08：初始化 plotter_tool 包框架，新增配置模块 config.py、默认 config.json，并在 pyproject.toml 中注册 CLI 入口。
- 2025-11-08：实现 svg_font 排版逻辑与 layout 子命令，支持缺字表格输出。
- 2025-11-08：完成 gcode_post 模块与 post 子命令，支持自动补速、蘸墨/换纸统计。
