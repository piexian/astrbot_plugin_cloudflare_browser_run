# 更新日志

## v1.0.1

- 超长工具结果不再返回 `truncated=true` 预览，改为将完整结果保存为 JSON 文件。
- 工具返回中新增结果文件信息：`file_path`、`file_size_bytes`、`file_size`、`content_chars`，并提示使用 AstrBot 文件搜索/读取工具继续查看。
- 超长 Cloudflare Browser 结果统一保存到插件持久化目录：`data/plugin_data/astrbot_plugin_cloudflare_browser_run/cloudflare_browser_results/`。
- 增强插件持久化目录获取逻辑，兼容 AstrBot 运行环境和本地开发环境的回退路径。
- 更新 `_conf_schema.json` 和 `README.md` 中的输出设置说明，明确超长结果会写入本地文件。
