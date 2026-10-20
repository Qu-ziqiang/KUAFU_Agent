# 前端 · 每日 AI 新闻助手

**纯静态页面，零构建、零运行时依赖**，直接用 nginx 托管即可，不需要 Node 构建链。

## 功能

- 填写身份与订阅偏好：关注话题、关键词、简报语言、检索条数（1–30）、自定义要求
- 一键「生成今日简报」：SSE 流式实时渲染正文与来源卡片，边搜边出
- 每条来源都可**直达原始链接**；OCR 条目带徽章，附可信度标记
- 历史简报列表与详情查看

## 目录结构

```
frontend/
  index.html          页面骨架
  style.css           样式
  app.js              前端逻辑（fetch + SSE 流式）
  API_CONTRACT.md     前后端接口契约
  mock-server.mjs     本地预览服务（见下）
  nginx/
    kuafu-agent.conf  生产 nginx 站点配置
```

## 本地预览

正常跑后端（`uvicorn app.main:app --port 8080`）后直接访问 <http://127.0.0.1:8080/> 即可，FastAPI 会同源托管本页面，**无需另起前端服务**。

需要单独调试前端时，可用零依赖的预览服务：

```bash
cd frontend
node mock-server.mjs          # http://localhost:5173
```

它同时托管静态页并处理 `/api/*`：后端在 `127.0.0.1:8080` 存活时**透传真后端**（SSE 不缓冲），后端未启动则自动回落离线假数据，页面不会白屏。每 5 秒探测一次后端状态，无需重启。

环境变量：`BACKEND_URL`（默认 `http://127.0.0.1:8080`）、`MOCK_ONLY=1`（强制离线假数据）、`PORT`（默认 5173）。

## 后端接口

统一返回 `{ code, data, message }`，`code=0` 为成功；简报 `status` 为 `ok` / `partial` / `failed`；列表 `data` 为数组。

生成走 SSE：`POST /api/users/{id}/briefings/stream`，事件包括 `start` `status` `tool_call` `tool_result` `item_draft` `sources_update` `first_ready` `done` `saved` `error`。

完整字段定义见 [`API_CONTRACT.md`](./API_CONTRACT.md)。

## 生产部署

线上由 nginx 同一端口托管静态页并反代 `/api/` 到后端。

关键配置（详见 [`nginx/kuafu-agent.conf`](./nginx/kuafu-agent.conf)）：

- SSE 位置必须 `proxy_buffering off` + `gzip off` + 长超时，否则流式会被缓冲成一次性返回
- 不要写 `listen [::]:<port>`：多数云主机没开 IPv6，写了 nginx 起不来
- 后端绑 `127.0.0.1`，不直接对外暴露

现状：已上线 <http://47.109.203.241:3001/>，公网可访问。
