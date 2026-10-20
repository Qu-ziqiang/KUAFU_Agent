# 每日 AI 新闻助手

KUAFUAI Agent 开发实习题 · 选项 A（Daily AI News Assistant）

用户填写话题、关键词、语言、检索条数和自定义要求后，由大模型以 ReAct 方式自主调用工具，联网检索新闻并生成可溯源的简报。页面通过 SSE 流式展示进度。

**在线演示**：http://47.109.203.241:3001/  
打开页面填好偏好，点「生成今日简报」即可，无需本地安装。

仓库地址：https://github.com/Qu-ziqiang/KUAFU_Agent

---

## 5 分钟跑起来

### 环境

- Python 3.10+
- DeepSeek（或任意 OpenAI 兼容）API Key
- Tavily Search API Key

### 安装

```bash
git clone https://github.com/Qu-ziqiang/KUAFU_Agent.git
cd KUAFU_Agent

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

### 配置

```bash
# Windows
copy .env.example .env
# macOS / Linux
# cp .env.example .env
```

编辑 `.env`，至少填写：

```env
OPENAI_API_KEY=你的大模型密钥
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-flash
TAVILY_API_KEY=你的 Tavily 密钥
```

密钥只放在本地 `.env` 里，不要提交到 Git。

### 启动

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

浏览器打开：http://127.0.0.1:8080/

填称呼、话题、关键词，直接点「生成今日简报」（会自动保存偏好）。

### 跑测试

```bash
pytest -q
```

---

## 项目做什么

1. 创建用户身份（UUID），持久化订阅偏好  
2. SSE 流式生成简报：正文逐条出现，下方可点击溯源  
3. 五个工具由模型决定调用顺序：`web_search`、`fetch_webpage`、`ocr_document`、`trace_source`、`compose_briefing`  
4. 监督层卡住条数、搜索轮数、国内外过滤与超时；不符合要求的条目丢掉后自动补搜  
5. 默认国内检索；自定义要求写「只看国外」时切换更宽的轮数与时限  

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python、FastAPI、Uvicorn、SQLite |
| Agent | LangGraph（ReAct） |
| 模型 | OpenAI 兼容 Chat Completions（推荐 DeepSeek） |
| 检索 | Tavily |
| 前端 | HTML / CSS / JS + SSE |

---

## Agent 流程简述

```text
prepare → agent ⇄ tools → 逐条改写 → 总览 → 落库
                ↘ review（链接、条数、标题等硬规则）
```

工具顺序由模型决定；停机条件、相关性、条数与预算由监督层保证。  
新闻只存在当次运行与当次简报中，不维护本地新闻语料库。

最大搜索轮次约为「检索条数」的一半；凑不齐时会明确提示，不会用无关新闻凑数。

---

## 主要接口

统一格式：`{ "code": 0, "message": "ok", "data": ... }`

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/health` | 服务与密钥是否就绪 |
| POST | `/api/users` | 创建用户 |
| GET | `/api/users/{id}` | 用户与偏好 |
| PUT | `/api/users/{id}/preferences` | 保存偏好 |
| GET | `/api/users/{id}/briefings` | 历史简报 |
| GET | `/api/briefings/{id}` | 简报详情与工具轨迹 |
| POST | `/api/users/{id}/briefings/stream` | SSE 生成简报 |

SSE 事件包括：`start`、`status`、`tool_call`、`tool_result`、`sources_update`、`first_ready`、`done`、`saved`、`error`。

更细的字段说明见 `frontend/API_CONTRACT.md`。

---

## 目录结构

```text
app/
  main.py              # FastAPI 入口，托管页面与 API
  config.py            # 环境变量与检索预算
  agent/               # LangGraph、工具、提示词、逐条改写
  services/briefing.py # 用户、偏好、落库
frontend/              # 静态页面与 nginx 示例
sql/schema.sql
tests/
deploy/                # 服务器安装与部署脚本（可选）
.env.example
requirements.txt
```

---

## 常见问题

- **点生成提示检索未配置**：检查 `.env` 是否填写 `TAVILY_API_KEY`，改完后重启服务。  
- **pip 安装慢**：可用清华源  
  `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`  
- **端口占用**：换端口  
  `python -m uvicorn app.main:app --host 127.0.0.1 --port 9000`  

图片 OCR 依赖本机 Tesseract（可选）；普通网页新闻不需要。
