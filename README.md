# Cloudflare 云抓取 (astrbot_plugin_cloudflare_browser_run)

`astrbot_plugin_cloudflare_browser_run` 是一个 AstrBot LLM Tool 插件，用于通过 Cloudflare Browser Rendering / Browser Run 抓取网页内容。

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

1. 进入 Cloudflare Dashboard 的 API Tokens 页面。
2. 选择创建自定义 Token。
3. 添加 Account 权限：`Browser Rendering` -> `Edit`。
4. Account Resources 选择需要使用 Browser Run 的账号。

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
| `max_output_chars` | `12000` | 工具返回给 LLM 的最大字符数 |

### 工具开关

| 配置项 | 默认值 | 工具 |
|--------|--------|------|
| `enable_markdown` | `true` | `cf_browser_markdown` |
| `enable_content` | `true` | `cf_browser_content` |
| `enable_links` | `true` | `cf_browser_links` |
| `enable_scrape` | `true` | `cf_browser_scrape` |
| `enable_json` | `true` | `cf_browser_json` |
| `enable_crawl_start` | `true` | `cf_browser_crawl_start` |
| `enable_crawl_status` | `true` | `cf_browser_crawl_status` |
| `enable_crawl_cancel` | `true` | `cf_browser_crawl_cancel` |

修改工具开关后需要重载插件。

## 工具

| 工具名 | 用途 |
|--------|------|
| `cf_browser_markdown` | 抓取 URL 或 HTML 并返回 Markdown |
| `cf_browser_content` | 抓取 URL 或 HTML 并返回 HTML |
| `cf_browser_links` | 提取页面链接 |
| `cf_browser_scrape` | 按 CSS selector 抓取页面元素 |
| `cf_browser_json` | 抽取结构化 JSON |
| `cf_browser_crawl_start` | 启动异步 Crawl 任务 |
| `cf_browser_crawl_status` | 查询 Crawl 任务状态和结果 |
| `cf_browser_crawl_cancel` | 取消 Crawl 任务 |

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

常用可选参数：

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

## 敏感信息

`api_token`、`set_extra_http_headers`、`authenticate`、`cookies` 等字段会在错误信息中脱敏。请只在可信环境中配置和调用包含认证信息的抓取任务。

## 目录结构

```text
astrbot_plugin_cloudflare_browser_run/
├── main.py
├── logo.png
├── tools/
│   ├── __init__.py
│   └── cloudflare_browser.py
├── metadata.yaml
├── _conf_schema.json
└── README.md
```

## 许可

AGPL-3.0-or-later
