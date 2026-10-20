# KUAFUAI 每日 AI 新闻助手 · 前端 API 契约

> **这份文档是后端（Cursor 实现）与前端（本仓库静态页）的唯一真相来源。** 后端严格按此实现，字段名、路径、SSE 事件类型都不要改；前端只消费下面的接口，不感知工具实现。

## 0. 统一约定

- **产品定位：联网新闻搜索引擎。** Agent 不把新闻「落本地文件」，而是**实时联网检索 → 抓取正文 →（命中文档时）OCR → 溯源核验 → 汇总成简报**。原始资讯始终以「外链」形态存在，前端可一键抵达原始链接。后端只需持久化「用户偏好」和「用户自己的简报」，不要持久化原始新闻语料。
- Base URL（生产）：由 nginx 在 `:3001` 反代，前端统一请求以 `/api` 开头。
- 统一响应体：`{ "code": 0, "data": ..., "message": "..." }`
  - `code === 0` 表示成功；非 0 视为错误，前端取 `message` 显示。
  - **简报列表接口 `data` 直接是数组**，不是 `{ list, total }` 包裹对象。
- 鉴权：本期无登录态，靠前端 `localStorage` 里的 `userId` 区分用户；后端按需自行加校验。
- **用户 `id` 是 UUID 字符串**（不是数字）。表征形式的改变要点：
  - 前端 `localStorage` 的 `kuafu_user_id` **按字符串原样存取**，不再 `Number()` 转换，所有路径参数都当字符串拼接。
  - **旧的数字 id 仍可被 `GET /api/users/{旧id}` 查到**（后端做了兼容），响应里 `data.id` 返回的是新的 UUID；前端拿到后**立刻覆盖**本地保存的 id。
  - `GET /api/users/{id}` 的 `preferences` 会带回 `recall_count` 与 `custom_prompt`，前端用来回填表单。

---

## 1. 健康检查

`GET /api/health`

响应：
```json
{ "code": 0, "data": { "llm_ready": true, "search_ready": true, "model": "gpt-4o-mini", "search_backend": "tavily" }, "message": "ok" }
```
- `llm_ready`：是否已配置可用 LLM（如 `OPENAI_API_KEY`）。
- `search_ready`：联网检索后端是否可用（如搜索 API key 已配 / RSS 聚合可用）。
- `search_backend`：当前使用的检索后端名称（如 `tavily` / `bocha` / `rss`）。

> 说明：此端点**前端已不再消费**（页面不展示模型/检索状态），仅保留给运维探活与部署自检使用。接口形状与路径保持不变，后端无需改动。

---

## 2. 用户 & 订阅偏好

### 2.1 创建用户
`POST /api/users`

请求体：
```json
{ "name": "林夏", "email": "lin@example.com" }
```
（邮箱可为 `null` 或空串。）

响应：
```json
{
  "code": 0,
  "data": {
    "id": 1,
    "name": "林夏",
    "email": "lin@example.com",
    "preferences": { "topics": [], "keywords": [], "language": "zh" }
  },
  "message": "ok"
}
```
> 前端首次保存时若 `userId` 不存在就调这个接口，拿到 `id` 存进 `localStorage`。

### 2.2 获取用户
`GET /api/users/{id}`

响应：同 2.1 的 `data` 结构（含 `preferences`）。
> 若用户不存在，返回 `code !== 0`，前端会清空 `localStorage` 让用户重新保存。

### 2.3 更新订阅偏好
`PUT /api/users/{id}/preferences`

请求体：
```json
{
  "topics": ["歌手"],
  "keywords": ["陶喆"],
  "language": "zh",
  "recall_count": 5,
  "custom_prompt": "多写演出和综艺，少写购票广告"
}
```
字段说明：
- `topics` / `keywords`：前端把输入框里的文案按**逗号或中文逗号**拆开，作为字符串数组提交。
- `language`：`zh` / `en`。
- `recall_count`：检索条数，**数字，范围 1–30，默认 5**（后端 `RECALL_LIMIT = 30`，`app/schemas.py`）。
  - 前端用 `<input type="number" min="1" max="30" step="1">`，越界值**夹取**到区间内（<1 取 1，>30 取 30）。
  - 空值 / 非数字（`abc`）时前端回落 **5**，并把输入框回写成 `5`。
  - 后端入库前同样会 `min(30, max(1, v))`，两端一致。
- `custom_prompt`：给模型的额外要求，多行文本，最多 500 字。**前端原样提交，不做任何改写（不 trim、不改标点）**；无内容时为空字符串。
  - 注意：后端入库时会 `.strip()[:500]`，属后端自己的规范化，与前端无关。

响应：
```json
{ "code": 0, "data": { "topics": [...], "keywords": [...], "language": "zh", "recall_count": 5, "custom_prompt": "..." }, "message": "ok" }
```

> **P2（无害偏差）**：`data` 也可以是整份用户对象（含 `id` / `name` / `preferences`）。前端保存时不依赖响应体形状，两种都可。

> **这两个字段随「每次请求」动态取当前输入框的值**：点「保存偏好」时 PUT 带上它们；点「生成今日简报」时也会带上同一份 JSON 去请求 `/briefings/stream`（见 3.3），**不使用上一次保存的旧值**。

---

## 3. 简报

> 简报 = 一次「联网检索 + AI 汇总」的产物。每个条目都带**原始链接（url）**与**来源（source）**，前端据此做「溯源」——点击即打开原网页。命中 PDF / 图片 / 扫描件时，条目带 `ocr` 标记，表示正文经 OCR 解析得到。

### 3.1 历史列表
`GET /api/users/{id}/briefings`

响应（`data` 为数组，按 `created_at` 倒序）：
```json
{
  "code": 0,
  "data": [
    {
      "id": 1,
      "title": "每日 AI 简报 · 2026/10/19",
      "created_at": "2026-10-19T09:00:00Z",
      "status": "ok",
      "item_count": 8,
      "query": "AI agent 开源模型"
    }
  ],
  "message": "ok"
}
```
- `item_count`：本期命中的新闻条目数（含 OCR 文档数）。
- `query`：本期实际检索词（溯源/复用用）。

`status` 取值（与后端 LangGraph review 对齐）：

| 值 | 含义 |
|---|---|
| `ok` | 简报检查通过并落库 |
| `partial` | 有结果但检查未完全通过（如部分来源抓取失败、条目不足），仍可展示 |
| `failed` | 生成失败 |

> **P2（无害偏差）**：`id` 可为整数或字符串；前端只把它当路径参数使用，不依赖类型。

### 3.2 单篇详情
`GET /api/briefings/{id}`

响应：
```json
{
  "code": 0,
  "data": {
    "id": 1,
    "title": "每日 AI 简报 · 2026/10/19",
    "created_at": "2026-10-19T09:00:00Z",
    "status": "ok",
    "query": "AI agent 开源模型",
    "content": "# 今日 AI 速览\n\n- 开源模型发布新版本……\n- 多家 Agent 框架竞争加剧……",
    "items": [
      {
        "title": "某开源模型发布新版本，推理成本再降",
        "url": "https://www.jiqizhixin.com/articles/abc123",
        "source": "机器之心",
        "published_at": "2026-10-19T08:00:00Z",
        "summary": "该模型在长上下文与工具调用上取得进展，已开放权重。",
        "ocr": false,
        "ocr_source_type": null,
        "credibility": "high"
      },
      {
        "title": "2026 全球 AI 安全报告（PDF）",
        "url": "https://example.org/reports/ai-safety-2026.pdf",
        "source": "某研究机构",
        "published_at": "2026-10-18T12:00:00Z",
        "summary": "（OCR 解析）报告第 3 章指出多智能体协作的可靠性仍是难点。",
        "ocr": true,
        "ocr_source_type": "pdf",
        "credibility": "medium"
      }
    ],
    "steps": [
      { "tool_name": "web_search", "arguments": "{\"query\":\"AI agent 开源模型\"}", "result": "...", "ok": true },
      { "tool_name": "fetch_webpage", "arguments": "{\"url\":\"https://www.jiqizhixin.com/articles/abc123\"}", "result": "...", "ok": true },
      { "tool_name": "ocr_document", "arguments": "{\"url\":\"https://example.org/reports/ai-safety-2026.pdf\"}", "result": "...", "ok": true },
      { "tool_name": "trace_source", "arguments": "{\"url\":\"https://www.jiqizhixin.com/articles/abc123\"}", "result": "...", "ok": true }
    ],
    "error": null
  },
  "message": "ok"
}
```

字段说明：
- `content`：LLM 生成的简报正文（Markdown），前端做轻量渲染。
- `items`：**结构化新闻条目，可溯源**。每个 item：
  - `title`：条目标题
  - `url`：**原始链接**，前端渲染为可点击外链（溯源入口，`target="_blank"` 打开原网页）
  - `source`：来源名（站点 / 媒体 / 机构）
  - `published_at`：发布时间（ISO8601，可为 `null`）
  - `summary`：条目摘要；**若 `ocr=true`，此处为 OCR 提取的正文片段**
  - `ocr`：是否经 OCR 解析（`true` 时前端展示「OCR」角标）
  - `ocr_source_type`：`pdf` / `image` / `null`，OCR 命中时的来源类型
  - `credibility`：溯源可信度 `high` / `medium` / `low`（可选，溯源工具给出）
- `steps`：Agent 工具调用轨迹，**顺序由 LLM 决定**（选项 A 核心要求①），前端原样展示 `tool_name` / `arguments` / `result`。
- `status`：同 3.1，为 `ok` / `partial` / `failed`。
- `error`：失败或 partial 时的说明；成功可为 `null`。

### 3.3 流式生成（SSE）
`POST /api/users/{id}/briefings/stream`

- **请求体**：`Content-Type: application/json`，内容为当前输入框中的偏好（与 2.3 同构，**不是上次保存的旧值、也不是空**）：
  ```json
  { "topics": ["歌手"], "keywords": ["陶喆"], "language": "zh", "recall_count": 5, "custom_prompt": "多写演出和综艺，少写购票广告" }
  ```
- 前端会先确保用户存在且偏好已保存（尚未建用户时自动创建），再带上这份 JSON 请求本接口。
- 响应：`Content-Type: text/event-stream`，每行格式 `data: <json>\n\n`。
- 事件类型（按发生顺序；前端可只消费自己需要的，但后端/mock 应能发出下列类型）：

| type | 说明 | 前端是否必用 |
|---|---|---|
| `start` | 一轮开始，可带 `query` / `max_steps` | 可选；忽略不影响 |
| `tool_call` | 即将/正在调用某工具（可与 `tool_result` 成对） | 可选；忽略不影响 |
| `tool_result` | 一次工具调用结果（可多次，含 `tool_name`/`arguments`/`result`/`ok`） | **必用**：展示轨迹 |
| `done` | 图/循环结束，带标题、正文、items 等中间结果 | 可选；忽略不影响 |
| `error` | 失败，`message` 为原因 | **必用** |
| `saved` | 落库完成，`briefing` 同 3.2 的 `data`（含 `items`、`steps`） | **必用**：渲染简报 |

示例：

```json
{ "type": "start", "query": "AI agent 开源模型", "max_steps": 8 }
```

```json
{ "type": "tool_result", "tool_name": "web_search", "arguments": "{\"query\":\"AI agent 开源模型\"}", "result": "命中 12 条候选", "ok": true }
```

```json
{ "type": "tool_result", "tool_name": "ocr_document", "arguments": "{\"url\":\"https://example.org/reports/ai-safety-2026.pdf\"}", "result": "OCR 提取 3 段正文", "ok": true }
```

```json
{ "type": "done", "title": "...", "content": "...", "items": [ "...": "同 3.2 items" ], "status": "ok", "review": "通过" }
```

```json
{ "type": "error", "message": "未配置 OPENAI_API_KEY 或搜索后端不可用" }
```

```json
{ "type": "saved", "briefing": { "...": "同 3.2 data" } }
```

> 前端用 `fetch` + `ReadableStream` 读取；后端务必保持连接、逐条 flush、最后正常结束。

---

## 4. 工具集（至少 5 个，联网检索型，供 LLM ReAct 调度）

后端需向 LLM 暴露至少以下 5 个**联网检索型**工具（签名自定，前端不感知实现，只展示 `tool_name` / `arguments`）。**工具调用顺序由 LLM 决定**，不是硬编码线性流程（要求①）。

| 工具 | 作用 | 何时由 LLM 触发 |
|---|---|---|
| `web_search(query)` | 联网检索：调用搜索后端（Tavily / 博查 / SerpAPI / RSS 聚合）返回候选条目（标题+url+source+摘要） | 起点：按用户偏好构造查询 |
| `fetch_webpage(url)` | 抓取网页正文：去噪、提取结构化正文 | 需要阅读某条候选详情时 |
| `ocr_document(url)` | **OCR 解析**：对 PDF / 图片 / 扫描件类 url 做 OCR，返回可检索文本 | 候选是文档/图片、fetch 拿不到正文时 |
| `trace_source(url)` | **溯源核验**：校验 url 的来源可信度、发布时间、作者，返回 source 名 + 原始链接 + credibility | 落库前核验来源可靠性 |
| `compose_briefing(items)` | 汇总成稿：把筛选后的 items 汇总为 Markdown 简报并触发落库 | 收尾：生成 `content` + `items` |

> 检索后端选型建议（此前已调研）：中文源为主干（机器之心 `https://www.jiqizhixin.com/rss`、量子位 `https://www.qbitai.com/feed`、36氪 `https://36kr.com/feed`），arXiv（`https://rss.arxiv.org/rss/cs.AI`）境内可直连，英文源（OpenAI/DeepMind/HF 的 RSS）作为可选增强。成都服务器直连不稳的源走搜索 API（Tavily/博查）兜底。**切勿把原始语料落本地文件**——`web_search`/`fetch_webpage` 的结果只进当次简报对象，不写磁盘。

---

## 5. 与早期草稿 / 文件系统版型差异（以本契约为准）

| 早期文件型 | 本契约（联网搜索引擎型） | 处理 |
|---|---|---|
| 工具 list_dir / read_file / search_content / write_file / bash | 工具 web_search / fetch_webpage / ocr_document / trace_source / compose_briefing | **以本契约为准**，不再操作本地文件 |
| 新闻语料落 `/data/news` 本地目录 | **不落本地**，实时检索、仅当次简报持有 | **以本契约为准** |
| 简报只有 `content`+`steps` | 简报新增 `items[]`：每条带 `url`(原始链接)/`source`/`ocr`/`credibility`，支持溯源与 OCR 角标 | **以本契约为准** |
| 响应字段 `msg` | 响应字段 **`message`**（与现网 `app.js` / 后端一致） | **以本契约为准** |
| 简报 `status: "done"` | **`ok` / `partial` / `failed`** | **以本契约为准** |
| SSE 只有 tool_result/error/saved | 另含 **`start` / `tool_call` / `done`** | **以本契约为准**；前端可忽略非必用事件 |
| 偏好含 `channels` / `frequency` | 偏好含 `language`（zh/en），无推送渠道/频率 | **以本契约为准**，不实现推送 |
| 列表接口分页 `{list,total}` | 列表 `data` 直接为数组 | **以本契约为准** |
| 生成用「返回任务 id + 轮询」 | 生成用 **SSE 流式**（`/briefings/stream`） | **以本契约为准** |

---

## 6. 本地预览（无真实后端）

仓库内含零依赖 `mock-server.mjs`，实现上述全部接口 + 模拟 SSE 检索轨迹（含 OCR / 溯源事件）：
```bash
cd frontend
node mock-server.mjs      # http://localhost:5173
```
打开即可体验：填偏好 → 「生成今日简报」实时看到 `web_search` → `fetch_webpage` → `ocr_document` → `trace_source` 的工具轨迹 → 落库简报（带可点击原始链接与 OCR 角标）。
