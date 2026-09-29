# 更新日志

## 未发布

- 新增 SDK v1 公开服务门面：其他插件可通过 `main.get_service(api_version=1)` 取得 `CloudflareBrowserService`，
  公开 `fetch/markdown/content/links/scrape/json/crawl_start/crawl_status/crawl_cancel` 具名方法；
  SDK 返回完整结果（不截断、不落结果文件、不上传沙箱），限长与文件桥接仅保留在旧 LLM Tool 展示层。
- 请求构造/校验/执行提取为 `CloudflareBrowserRuntime` 具名共享业务入口，Main、所有 Tool 与 SDK 共用同一 Runtime 实例；
  `build_tools` 新增向后兼容的可选 `runtime` 参数。
- 新增 `initialize()` 状态节点与状态查询：`get_status()/capabilities()/wait_ready()` 零副作用（不新建 Runtime、不读写文件、不联网）；
  缺 `account_id`/`api_token` 时状态为 `unavailable`（not_configured）；卸载后旧服务拒绝新调用（service_closed）。
- crawl `job_id` 现校验为单个安全路径段，拒绝路径/查询注入；原有八类 Cloudflare API、限长桥接与沙盒同步行为不变。

## v1.1.2

- 修复兜底响应可能超过 `max_output_chars`：preview 改为按序列化后元数据预留空间动态截断，兜底 JSON 整体不超限。
- 沙盒同步失败消息中的错误详情截断至 200 字符，避免长错误信息吃掉 preview 预算。

## v1.1.1

- 修复沙盒运行时下超长结果文件不可达：检测到 `computer_use_runtime=sandbox` 时自动将结果文件上传到当前会话沙盒，返回沙盒内 `file_path`（附 `host_file_path`、`sandbox_synced`）。
- 沙盒上传失败或未启用 Computer Use 运行时时，返回附带 `preview` 内容预览兜底。

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
