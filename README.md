# Cloudflare 云抓取 (astrbot_plugin_cloudflare_browser_run)

通过 Cloudflare Browser Rendering / Browser Run 抓取网页内容。

## 功能

- 将 URL 或 HTML 转为 Markdown
- 获取渲染后的 HTML 内容
- 提取页面链接
- 使用 CSS selector 抓取页面元素
- 抽取结构化 JSON
- 启动、查询和取消异步 Crawl 任务

## 环境要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Python | >= 3.10 | |
| AstrBot | >= v4.9.2 | LLM Tool 插件 |

**平台支持**: 全平台（无限制）

## 安装

### 两种方式

1. 在 AstrBot 插件市场搜索 `Cloudflare云抓取` 点击安装
2. 在插件界面右下角点击加号，选择从链接安装，输入 `https://github.com/piexian/astrbot_plugin_cloudflare_browser_run`

## 配置

### 连接设置

| 配置项 | 必填 | 说明 |
|--------|------|------|
| `account_id` | 是 | Cloudflare Account ID |
| `api_token` | 是 | Cloudflare API Token，需要 `Browser Rendering - Edit` 权限 |

API Token 创建方式：

1. 打开 [Cloudflare API Tokens 页面](https://dash.cloudflare.com/?to=/:account/api-tokens)。
2. 选择创建自定义 Token。可参考 [Cloudflare 创建 API Token 文档](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)。
3. 添加 Account 权限：`Browser Rendering` -> `Edit`。
4. Account Resources 选择需要使用 Browser Run 的账号。

只需要 `Edit` 权限；`Read` 权限不足以执行抓取、启动 Crawl 或取消 Crawl。
<img width="720" height="520" alt="image" src="https://github.com/user-attachments/assets/c3547ca1-d23b-4ce1-8034-779ee8075196" />


Browser Run 用量可在 [Cloudflare Dashboard 的 Browser Run 页面](https://dash.cloudflare.com/?to=/:account/workers/browser-run) 查看。

### 请求设置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `timeout_seconds` | `120` | Cloudflare API 请求超时时间，单位秒 |
| `default_cache_ttl` | `5` | 默认 `cacheTTL`，单位秒 |
| `default_render` | `false` | Crawl 默认是否启用浏览器渲染 |
| `max_crawl_limit` | `100` | 单次 Crawl 最大页数限制 |

### 输出设置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_output_chars` | `12000` | 工具直接返回给 LLM 的最大字符数；超出后完整结果会写入插件持久化目录 |

当抓取结果超过 `max_output_chars` 时，工具不会把完整内容塞回 LLM 上下文，而是返回一个小型 JSON，包含：

- `saved_to_file: true`
- `file_path`: 完整结果文件路径，位于 `data/plugin_data/astrbot_plugin_cloudflare_browser_run/cloudflare_browser_results/`
- `file_size_bytes` / `file_size`: 文件大小
- `content_chars`: 原始 JSON 字符数
- `suggested_tools`: 建议使用 `astrbot_grep_tool` 搜索文件，或使用 `astrbot_file_read_tool` 分段读取

沙盒适配：当 AstrBot 的 `computer_use_runtime` 为 `sandbox` 时，插件会自动把结果文件上传到当前会话沙盒，返回的 `file_path` 为沙盒内路径（同时附带 `host_file_path` 与 `sandbox_synced: true`），沙盒内的文件工具可直接读取；上传失败或未启用文件工具运行时时，返回会附带 `preview` 内容预览兜底。

工具启停使用 AstrBot 自带的 LLM Tool 管理功能控制。插件初始化时会注册全部工具。

## 工具

| 工具名 | 用途 | JS 渲染 |
|--------|------|---------|
| `cf_browser_markdown` | 抓取 URL 或 HTML 并返回 Markdown | ✅ 默认渲染 |
| `cf_browser_content` | 抓取 URL 或 HTML 并返回 HTML | ✅ 默认渲染 |
| `cf_browser_links` | 提取页面链接 | ✅ 默认渲染 |
| `cf_browser_scrape` | 按 CSS selector 抓取页面元素 | ✅ 默认渲染 |
| `cf_browser_json` | 抽取结构化 JSON | ✅ 默认渲染 |
| `cf_browser_crawl_start` | 启动异步 Crawl 任务 | ⚠️ 默认不渲染，需 `render: true` |
| `cf_browser_crawl_status` | 查询 Crawl 任务状态和结果 | — |
| `cf_browser_crawl_cancel` | 取消 Crawl 任务 | — |

### JavaScript 渲染说明

单页抓取工具（markdown/content/links/scrape/json）默认使用 headless Chrome 执行 JavaScript 后再提取内容，无需额外参数。

Crawl 工具默认 `render=false`（快速 HTML 抓取，不执行 JS，不计 browser hours）。需要抓取 JS 渲染页面时传 `render: true`，但会消耗 browser hours 且更慢。可通过配置项 `default_render` 调整默认值。

## SDK 服务接入（v1）

其他插件可通过 `get_service(api_version=1)` 获取公开服务门面，与 LLM Tool 共用同一份配置和 Runtime：

```python
def get_browser_service(context):
    meta = context.get_registered_star("astrbot_plugin_cloudflare_browser_run")
    if meta is None:
        raise RuntimeError("注册表中未发现浏览器插件，请检查安装与加载状态")
    if not meta.activated:
        raise RuntimeError("浏览器插件已停用")
    if meta.star_cls is None:
        raise RuntimeError("浏览器插件尚无可调用实例")
    getter = getattr(meta.star_cls, "get_service", None)
    if not callable(getter):
        raise RuntimeError("浏览器插件版本不支持 SDK，请升级")
    return getter(api_version=1)

service = get_browser_service(context)

status = service.get_status()
# 同步本地快照：{api_version, instance_id, state, ready, reason}
# state 为 initializing / ready / unavailable / closing / closed；
# 缺 account_id / api_token 时为 unavailable（reason=not_configured）
status = await service.wait_ready(timeout=10)
# 超时抛 TimeoutError；服务关闭后抛 RuntimeError（code=service_closed）

result = await service.fetch("https://example.com")            # markdown 的便捷入口
result = await service.markdown(url="https://example.com", cache_ttl=60)
result = await service.content(url="https://example.com")
result = await service.links(url="https://example.com", visible_links_only=True)
result = await service.scrape(url="https://example.com", elements=[{"selector": "h1"}])
result = await service.json(url="https://example.com", prompt="提取标题")
job = await service.crawl_start("https://example.com", limit=20)
detail = await service.crawl_status(job["job_id"])
await service.crawl_cancel(job["job_id"])
```

说明：

- 页面方法返回含 `type`、`url`、`result` 的字典；`crawl_start` 返回 `job_id`，查询和取消结果保留远端业务字段。
- SDK 返回完整结果：不截断、不落结果文件、不上传沙箱；限长与文件桥接仅是旧 LLM Tool 的展示行为。
- `service.capabilities()` 返回 `{"api_version": 1, "features": ["web.fetch", "browser.markdown", "browser.content", "browser.links", "browser.scrape", "browser.json", "browser.crawl"]}`。
- 版本只接受整数 `1`（不接受布尔值），不兼容时错误码为 `unsupported_version`；初始化未完成或缺凭据时，业务调用错误码为 `not_ready`，不会请求远端。
- 不在插件 `initialize()` 中等待依赖就绪；以上等待和业务调用用于事件处理或后台业务协程。
- 参数错误抛 `CloudflareParamError`（ValueError 子类，消息与工具一致）；Cloudflare API 错误抛 `CloudflareAPIError`（token / headers / cookies 等已脱敏）。
- 插件卸载后旧服务引用永久失效：业务调用抛 `RuntimeError`（code=service_closed），`get_status()` 仍可查询；重载后新服务的 `instance_id` 不同。
- crawl `job_id` 必须是单个安全路径段（自动拒绝路径/查询注入）；爬取额度 `max_crawl_limit`、缓存 TTL 与枚举校验同样适用于 SDK。

## 示例

### 抓取 Markdown

```json
{
  "url": "https://example.com"
}
```

### 提取链接

```json
{
  "url": "https://example.com",
  "visible_links_only": true
}
```

### 抓取页面元素

```json
{
  "url": "https://example.com",
  "elements": [
    {"selector": "h1"},
    {"selector": "article"}
  ]
}
```

### 启动 Crawl

```json
{
  "url": "https://example.com",
  "limit": 20,
  "formats": ["markdown"],
  "render": false
}
```

返回 `job_id` 后，可使用 `cf_browser_crawl_status` 查询结果。

## 可选抓取参数

单页抓取工具常用可选参数：

- `cache_ttl`
- `goto_options`
- `wait_for_selector`
- `wait_for_timeout`
- `viewport`
- `set_javascript_enabled`
- `user_agent`
- `allow_resource_types`
- `reject_resource_types`
- `allow_request_pattern`
- `reject_request_pattern`
- `set_extra_http_headers`
- `authenticate`
- `cookies`
- `add_script_tag`
- `add_style_tag`

Crawl 工具常用可选参数：

- `cache_ttl`
- `limit`
- `depth`
- `formats`
- `render`
- `source`
- `max_age`
- `modified_since`
- `crawl_purposes`
- `options`
- `json_options`
- `goto_options`（仅 `render=true` 时使用）
- `wait_for_selector`（仅 `render=true` 时使用）
- `wait_for_timeout`（仅 `render=true` 时使用）
- `viewport`（仅 `render=true` 时使用）
- `action_timeout`（仅 `render=true` 时使用）
- `best_attempt`（仅 `render=true` 时使用）
- `set_javascript_enabled`（仅 `render=true` 时使用）
- `emulate_media_type`（仅 `render=true` 时使用）
- `allow_resource_types`（仅 `render=true` 时使用）
- `reject_resource_types`（仅 `render=true` 时使用）
- `allow_request_pattern`（仅 `render=true` 时使用）
- `reject_request_pattern`（仅 `render=true` 时使用）
- `set_extra_http_headers`（仅 `render=true` 时使用）
- `authenticate`（仅 `render=true` 时使用）
- `cookies`（仅 `render=true` 时使用）
- `add_script_tag`（仅 `render=true` 时使用）
- `add_style_tag`（仅 `render=true` 时使用）

注意：

- `allow_request_pattern` 和 `reject_request_pattern` 不能同时使用。
- `allow_resource_types` 和 `reject_resource_types` 不能同时使用。
- `modified_since` 只用于 Crawl，必须是最近一年内且不晚于当前时间的 Unix 秒级时间戳。

## 敏感信息

`api_token`、`set_extra_http_headers`、`authenticate`、`cookies` 等字段会在错误信息中脱敏。请只在可信环境中配置和调用包含认证信息的抓取任务。

## 目录结构

```text
astrbot_plugin_cloudflare_browser_run/
├── main.py
├── plugin_service.py
├── logo.png
├── tools/
│   ├── __init__.py
│   └── cloudflare_browser.py
├── tests/
├── metadata.yaml
├── _conf_schema.json
└── README.md
```

## 相关链接

- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [Cloudflare Browser Run 文档](https://developers.cloudflare.com/browser-run/)
- [Cloudflare Browser Rendering 产品页](https://www.cloudflare.com/products/browser-rendering/)
- [Cloudflare API Token 文档](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)

## 许可

AGPL-3.0-or-later
