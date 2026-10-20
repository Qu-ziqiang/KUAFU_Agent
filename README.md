# 每日 AI 新闻助手

一个**联网新闻检索 Agent**：用户填好关注话题后，由大模型以 ReAct 方式**自主决定工具调用顺序**，联网检索 → 抓取正文 → 文档 OCR → 溯源核验 → 汇总成稿，输出一份每条都可点击回溯原文的 Markdown 简报，全程 SSE 流式返回。

> **在线演示**：<http://47.109.203.241:3001/>
> 阿里云轻量服务器 · nginx 托管静态页 + 反代 `/api` → FastAPI。打开后填偏好、点「生成今日简报」即可，无需安装。

---

## 它解决了什么

| 能力 | 说明 |
|---|---|
| 工具顺序由模型决定 | 不是固定流水线。给模型一组工具和当前上下文，由它自己决定下一步调哪个、调几次、什么时候收尾 |
| 联网检索 | 接 Tavily Search，新闻语料**不落本地**，实时检索 |
| 文档 OCR | `ocr_document` 解析 PDF / 图片里的正文，让非网页内容也能进入简报 |
| 可溯源 | 每条简报都带原始 `url`、来源站点、发布时间和可信度标注，页面上直接点开原文 |
| 流式生成 | SSE 边搜边推，搜到就先出草稿，不用等整轮跑完 |

---

## 5 分钟跑起来

### 0. 前置

- Python **3.10+**
- 两个 API Key：**大模型**（DeepSeek，1 分钟可申请）和 **Tavily Search**（联网检索，免费额度足够试用）

### 1. 安装依赖

```bash
git clone https://github.com/Qu-ziqiang/KUAFU_Agent.git
cd KUAFU_Agent

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> Windows 若报 `ensurepip` 不可用，先装 `python3-venv` 组件（或改用 `conda create`）。

### 2. 配置密钥

```bash
cp .env.example .env
```

编辑 `.env`，**两项必填**：

```ini
OPENAI_API_KEY=<你的大模型密钥>          # DeepSeek / 任意 OpenAI 兼容服务
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-flash

TAVILY_API_KEY=<你的 Tavily 密钥>        # 联网检索，缺少它生成会失败
```

其余项都有默认值，先不用动。密钥只走环境变量，不进代码、不进仓库。

### 3. 启动

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

### 4. 打开页面

浏览器访问 <http://127.0.0.1:8080/>

前端页面由 FastAPI 同源托管，无需另起前端服务。填好偏好 → 点「生成今日简报」→ 约 20–40 秒出稿。

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | — | **必填**。大模型密钥 |
| `OPENAI_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点，可换成任何兼容网关 |
| `OPENAI_MODEL` | `deepseek-flash` | 模型名 |
| `TAVILY_API_KEY` | — | **必填**。联网检索能力来源 |
| `MAX_AGENT_STEPS` | `6` | Agent 单轮最多决策几步 |
| `SEARCH_MAX_RESULTS` | `5` | 单次检索返回条数 |
| `SEARCH_MAX_ROUNDS` | `2` | 最多检索轮数 |
| `BRIEFING_MIN_ITEMS` | `3` | 简报最少条目数，不够会继续搜 |
| `BRIEFING_MAX_ITEMS` | `5` | 简报最多条目数，达到即召回 |
| `SEARCH_PREFERRED_DOMAINS` | 见 `.env.example` | 优先命中的站点域名，逗号分隔。默认偏中文科技媒体 |
| `FIRST_READY_SECONDS` | `60` | 体感红线：多少秒内必须先出一版 |
| `TOTAL_TIMEOUT_SECONDS` | `180` | 单轮总时限 |
| `DAILY_HOUR` | 空 | 设为 0–23 则每天该整点为所有用户自动跑一轮 |

---

## 目录结构

```
.
├── app/
│   ├── main.py                 FastAPI 入口，同源托管前端静态页
│   ├── config.py               配置与环境变量
│   ├── db.py                   SQLite 连接与建表
│   ├── schemas.py              请求/响应模型
│   ├── routers/api.py          HTTP 路由（前缀 /api）
│   ├── agent/
│   │   ├── graph.py            LangGraph 状态流转
│   │   ├── loop.py             Agent 主循环与 SSE 事件产出
│   │   ├── tools.py            工具定义与实现
│   │   ├── prompts.py          系统提示词
│   │   └── constraints.py      来源筛选与语言判定
│   └── services/               简报生成、检索编排
├── frontend/
│   ├── index.html / style.css / app.js   零构建静态前端
│   ├── API_CONTRACT.md         前后端接口契约
│   └── nginx/kuafu-agent.conf  生产站点配置（含 SSE 透传）
├── sql/schema.sql              建表语句，启动时自动执行
├── tests/                      pytest 用例
└── requirements.txt
```

---

## Agent 是怎么跑的

`app/agent/graph.py` 用 LangGraph 组织状态流转：

```text
prepare → agent → tools → agent
               ↘ review → 通过则收尾
                        → 不通过且仍有步数，把原因退回 agent 继续
```

模型可用的工具（**调用顺序由模型自己决定，不是写死的流水线**）：

| 工具 | 作用 |
|---|---|
| `web_search` | 联网检索新闻 |
| `fetch_webpage` | 抓取指定网页正文 |
| `ocr_document` | 解析 PDF / 图片中的文字 |
| `trace_source` | 核验来源与发布时间，补可信度标注 |
| `compose_briefing` | 汇总成 Markdown 简报并落库 |

体感设计：搜到就先往前端推 `item_draft` / `sources_update`，保证 60 秒内出 `first_ready`；总时限 180 秒兜底。

---

## 接口一览

统一响应包：`{"code": 0, "message": "ok", "data": {}}`；失败时 `code` 非 0，HTTP 状态与之一致（参数错误 400、不存在 404）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | 模型与检索服务是否就绪 |
| POST | `/api/users` | 创建用户（返回 UUID） |
| GET | `/api/users/{id}` | 查询用户与偏好 |
| PUT | `/api/users/{id}/preferences` | 保存话题、关键词、语言、检索条数、自定义要求 |
| GET | `/api/users/{id}/briefings` | 历史简报列表 |
| GET | `/api/briefings/{id}` | 简报详情（正文 + 来源条目） |
| POST | `/api/users/{id}/briefings/stream` | 跑一轮 Agent，**SSE 流式** |

SSE 事件：`start` `status` `tool_call` `tool_result` `item_draft` `sources_update` `first_ready` `done` `saved` `error`

完整字段定义见 [`frontend/API_CONTRACT.md`](./frontend/API_CONTRACT.md)。

---

## 数据存储

SQLite，默认文件 `data/kuafu.db`，启动时自动执行 `sql/schema.sql` 建表，无需手动初始化。

| 表 | 内容 |
|---|---|
| `users` | 用户身份（UUID） |
| `preferences` | 话题、关键词、语言、检索条数、自定义要求 |
| `briefings` | 简报正文与状态（`ok` / `partial` / `failed`） |
| `agent_steps` | 每一步的工具名、入参与结果，用于回看模型当时的决策 |

同一用户并发生成时，第二个请求会直接返回「已有简报正在生成」，避免两轮写冲突。

---

## 生产部署

线上用 nginx 托管静态页并反代 `/api` 到后端，两者同一端口对外。关键点：

- SSE 位置必须 `proxy_buffering off` + `gzip off` + 长超时，否则流式会被缓冲成一次性返回
- 后端以 systemd 运行，绑 `127.0.0.1:8080`，不直接对外暴露
- 密钥写在服务端 env 文件（权限 600），不随代码分发

参考配置：[`frontend/nginx/kuafu-agent.conf`](./frontend/nginx/kuafu-agent.conf)

---

## 常见问题

**页面能开，但点生成后报「检索未配置」** → `.env` 里缺 `TAVILY_API_KEY`，或改完没重启进程。

**改了 `.env` 没生效** → 环境变量优先级：进程已有同名变量时不会被覆盖，重启服务后再试。

**`pip install` 慢或超时** → 换国内源：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

**端口被占用** → 换端口启动：`uvicorn app.main:app --port 9000`
