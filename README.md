# Cloudflare 云抓取 (astrbot_plugin_cloudflare_browser_run)

通过 Cloudflare Browser Run（原 Browser Rendering）抓取网页内容。

## 功能

- 将 URL 或 HTML 转为 Markdown
- 获取渲染后的 HTML 内容
- 提取页面链接
- 使用 CSS selector 抓取页面元素
- 抽取结构化 JSON
- 启动、查询和取消异步 Crawl 任务
- 获取可访问性树，理解页面交互元素
- 通过 Snapshot 一次获取多种页面格式

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
| `api_token` | 是 | Cloudflare API Token，需要 `Browser Run - Edit`（旧界面 `Browser Rendering - Edit`）权限 |

API Token 创建方式：

1. 打开 [Cloudflare API Tokens 页面](https://dash.cloudflare.com/?to=/:account/api-tokens)。
2. 选择创建自定义 Token。可参考 [Cloudflare 创建 API Token 文档](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)。
3. 添加 Account 权限：`Browser Run`（旧界面 `Browser Rendering`）-> `Edit`。
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

安全请求遇到 429/5xx 时最多重试 2 次并遵守 `Retry-After`；等待超过 30 秒则不自动重试，Crawl 启动不重试。响应包含 `browser_ms_used` 时可据此观察浏览器用量。

### 输出设置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_output_chars` | `12000` | 工具直接返回给 LLM 的最大字符数；超出后写入可读结果文件 |
| `result_retention_hours` | `24` | 结果文件组保留时间 |
| `max_stored_results` | `50` | 每个结果目录最多保留的组数 |
超限返回包含 `file_path`、文件大小和 `suggested_tools`。Markdown 与 Crawl 使用可按行读取的内容文件，`raw_json_file_path` 保留原始 JSON。

Local 运行时优先保存到当前会话 `workspace/cloudflare_browser_results/`；沙盒运行时自动上传可读内容文件并返回沙盒路径。上传失败或未启用文件工具时附带 `preview` 兜底。
LLM 工具中的截图一律保存为图片文件并返回路径，不直接返回 Base64；SDK 保留 Cloudflare 原始响应。
每次写入前按保留时间和组数清理最旧结果，只处理插件生成的文件。

工具启停使用 AstrBot 自带的 LLM Tool 管理功能控制。插件初始化时会注册全部工具。

## 工具

| 工具 | 用途 |
|------|------|
| `cf_browser_fetch` | 单页渲染、读取与结构化提取 |
| `cf_browser_crawl` | 启动、查询和取消异步 Crawl |

默认读取 Markdown；Snapshot 默认返回 Markdown 和可访问性树。Fetch 默认执行 JavaScript，Crawl 默认 `render=false`。

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
result = await service.accessibility_tree(url="https://example.com")
result = await service.snapshot(url="https://example.com")  # 默认 Markdown + 可访问性树
job = await service.crawl_start("https://example.com", limit=20)
detail = await service.crawl_status(job["job_id"])
await service.crawl_cancel(job["job_id"])
```

说明：

- 页面方法返回含 `type`、`url`、`result` 的字典；`crawl_start` 返回 `job_id`，查询和取消结果保留远端业务字段。
- SDK 返回完整结果：不截断、不落结果文件、不上传沙箱；限长与文件桥接仅是 LLM Tool 的展示行为。
- `service.capabilities()` 返回 `web.fetch`、各单页能力及 `browser.crawl`；新增能力标识为 `browser.accessibility_tree` 和 `browser.snapshot`。
- 版本只接受整数 `1`（不接受布尔值），不兼容时错误码为 `unsupported_version`；初始化未完成或缺凭据时，业务调用错误码为 `not_ready`，不会请求远端。
- 不在插件 `initialize()` 中等待依赖就绪；以上等待和业务调用用于事件处理或后台业务协程。
- 参数错误抛 `CloudflareParamError`（ValueError 子类，消息与工具一致）；Cloudflare API 错误抛 `CloudflareAPIError`（token / headers / cookies 等已脱敏）。
- 插件卸载后旧服务引用永久失效：业务调用抛 `RuntimeError`（code=service_closed），`get_status()` 仍可查询；重载后新服务的 `instance_id` 不同。
- crawl `job_id` 必须是单个安全路径段（自动拒绝路径/查询注入）；爬取额度 `max_crawl_limit`、缓存 TTL 与枚举校验同样适用于 SDK。

## 示例

### 抓取 Markdown

```json
{
  "mode": "markdown",
  "url": "https://example.com"
}
```

### 提取链接

```json
{
  "mode": "links",
  "url": "https://example.com",
  "visible_links_only": true
}
```

### 抓取页面元素

```json
{
  "mode": "scrape",
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
  "action": "start",
  "url": "https://example.com",
  "limit": 20,
  "formats": ["markdown"],
  "render": false
}
```

返回 `job_id` 和 `next_action`。运行中每 5 秒用 `cf_browser_crawl(action=status, limit=1, cache_ttl=0)` 轮询，完成后再取完整记录。

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
