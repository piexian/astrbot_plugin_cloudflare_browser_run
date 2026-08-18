# 更新日志

## v1.1.0

- 补全全部工具参数 description，LLM 可理解各参数用途。
- 单页抓取工具 description 补充 JavaScript 渲染说明（默认执行 JS）。
- Crawl 工具 description 补充 `render` 参数说明（默认 false 不执行 JS）。
- 修复 Gemini 兼容性：移除 `set_extra_http_headers` 的 `additionalProperties`、移除 `exclude_external_links`/`visible_links_only` 的 `default` 字段。
- API 错误消息补充常见 HTTP 错误码（401/403/429/400/404/500）排查建议。

## v1.0.1

- 超长工具结果不再返回 `truncated=true` 预览，改为将完整结果保存为 JSON 文件。
- 工具返回中新增结果文件信息：`file_path`、`file_size_bytes`、`file_size`、`content_chars`，并提示使用 AstrBot 文件搜索/读取工具继续查看。
- 超长 Cloudflare Browser 结果统一保存到插件持久化目录：`data/plugin_data/astrbot_plugin_cloudflare_browser_run/cloudflare_browser_results/`。
- 增强插件持久化目录获取逻辑，兼容 AstrBot 运行环境和本地开发环境的回退路径。
- 更新 `_conf_schema.json` 和 `README.md` 中的输出设置说明，明确超长结果会写入本地文件。
