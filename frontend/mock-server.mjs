// 本地预览服务器（零依赖，非生产）。两种模式自动切换：
//   1) 后端在线  -> 静态页自己托管，/api/* 流式转发到真后端（默认 http://127.0.0.1:8080）
//   2) 后端离线  -> 退回内置 mock 假数据，页面依然可用
// 环境变量：
//   BACKEND_URL  真后端地址，默认 http://127.0.0.1:8080
//   MOCK_ONLY=1  强制只用假数据（不代理）
//   PORT         监听端口，默认 5173
// 运行：node mock-server.mjs

import http from 'node:http'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join, extname } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ROOT = __dirname // 本文件就在 frontend/ 下
const PORT = Number(process.env.PORT || 5173)
const UPSTREAM = process.env.BACKEND_URL || 'http://127.0.0.1:8080'
const MOCK_ONLY = process.env.MOCK_ONLY === '1'

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const readBody = (req) =>
  new Promise((resolve) => {
    let data = ''
    req.on('data', (c) => (data += c))
    req.on('end', () => {
      try { resolve(data ? JSON.parse(data) : {}) }
      catch { resolve({}) }
    })
  })

// ---------- 后端存活探测 ----------
let upstreamAlive = false
function probeUpstream() {
  return new Promise((resolve) => {
    let target
    try { target = new URL('/api/health', UPSTREAM) } catch { upstreamAlive = false; return resolve(false) }
    const r = http.request(target, { method: 'GET', timeout: 2000 }, (res) => {
      res.resume()
      const alive = res.statusCode >= 200 && res.statusCode < 500
      upstreamAlive = alive
      resolve(alive)
    })
    r.on('error', () => { upstreamAlive = false; resolve(false) })
    r.on('timeout', () => { r.destroy(); upstreamAlive = false; resolve(false) })
    r.end()
  })
}
setInterval(() => { probeUpstream() }, 5000).unref()

// ---------- 反向代理（SSE 流式透传） ----------
function proxyToBackend(req, res, pathAndQuery) {
  let target
  try { target = new URL(pathAndQuery, UPSTREAM) } catch { return sendJson(res, 502, { code: 502, data: null, message: '后端地址不合法' }) }

  const headers = { ...req.headers, host: target.host }
  const up = http.request(target, { method: req.method, headers }, (upRes) => {
    res.writeHead(upRes.statusCode || 502, upRes.headers)
    upRes.pipe(res) // 不缓冲，SSE 逐条流出
  })
  up.on('error', (err) => {
    upstreamAlive = false
    if (!res.headersSent) {
      sendJson(res, 502, { code: 502, data: null, message: `后端连接失败：${err.message}（后端是否在 ${UPSTREAM}？）` })
    } else {
      res.end()
    }
  })
  req.on('error', () => up.destroy())
  req.pipe(up)
}

// ---------- 内置 mock 数据 ----------
const db = { users: {}, seq: 1, briefings: {} }
const defaultPrefs = () => ({ topics: [], keywords: [], language: 'zh' })
const newUser = (name, email) => {
  const id = db.seq++
  db.users[id] = { id, name, email: email ?? null, preferences: defaultPrefs(), briefings: [] }
  return db.users[id]
}

function sendJson(res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(obj))
}
const ok = (res, data, message = 'ok') => sendJson(res, 200, { code: 0, data, message })

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)
  const p = url.pathname

  // ---------- 静态资源 ----------
  try {
    if (req.method === 'GET' && p === '/') {
      const buf = await readFile(join(ROOT, 'index.html'))
      res.writeHead(200, { 'Content-Type': MIME['.html'] }); return res.end(buf)
    }
    if (req.method === 'GET' && (p === '/static/style.css' || p === '/static/app.js')) {
      const f = p.split('/').pop()
      const buf = await readFile(join(ROOT, f))
      res.writeHead(200, { 'Content-Type': MIME[extname(f)] }); return res.end(buf)
    }
  } catch {
    return sendJson(res, 404, { code: 404, data: null, message: 'not found' })
  }

  // ---------- API ----------
  if (p.startsWith('/api/')) {
    if (!MOCK_ONLY) {
      if (!upstreamAlive) await probeUpstream()
      if (upstreamAlive) return proxyToBackend(req, res, url.pathname + (url.search || ''))
    }
  }

  // 以下为离线 mock
  if (p === '/api/health') return ok(res, { llm_ready: true, search_ready: true, model: 'mock-gpt-4o-mini', search_backend: 'mock-rss' })

  if (req.method === 'POST' && p === '/api/users') {
    const b = await readBody(req)
    const u = newUser(b.name, b.email)
    return ok(res, u)
  }

  const mUser = p.match(/^\/api\/users\/(\d+)$/)
  if (req.method === 'GET' && mUser) {
    const u = db.users[mUser[1]]
    return u ? ok(res, u) : sendJson(res, 200, { code: 404, data: null, message: 'user not found' })
  }

  const mPref = p.match(/^\/api\/users\/(\d+)\/preferences$/)
  if (req.method === 'PUT' && mPref) {
    const u = db.users[mPref[1]]
    if (!u) return sendJson(res, 200, { code: 404, data: null, message: 'user not found' })
    const b = await readBody(req)
    u.preferences = { ...defaultPrefs(), ...b }
    return ok(res, u.preferences)
  }

  const mList = p.match(/^\/api\/users\/(\d+)\/briefings$/)
  if (req.method === 'GET' && mList) {
    const u = db.users[mList[1]]
    const list = u
      ? u.briefings
          .map((id) => db.briefings[id])
          .filter(Boolean)
          .map((b) => ({
            id: b.id,
            title: b.title,
            created_at: b.created_at,
            status: b.status,
            item_count: (b.items || []).length,
            query: b.query || '',
          }))
      : []
    return ok(res, list)
  }

  const mOne = p.match(/^\/api\/briefings\/([\w-]+)$/)
  if (req.method === 'GET' && mOne) {
    const b = db.briefings[mOne[1]]
    return b ? ok(res, b) : sendJson(res, 200, { code: 404, data: null, message: 'briefing not found' })
  }

  const mStream = p.match(/^\/api\/users\/(\d+)\/briefings\/stream$/)
  if (req.method === 'POST' && mStream) {
    const userId = mStream[1]
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    })
    const push = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`)
    push({ type: 'start', query: 'AI agent 开源模型', max_steps: 8 })
    const tools = [
      ['web_search', '{"query":"AI agent 开源模型"}'],
      ['fetch_webpage', '{"url":"https://www.jiqizhixin.com/articles/abc123"}'],
      ['ocr_document', '{"url":"https://example.org/reports/ai-safety-2026.pdf"}'],
      ['trace_source', '{"url":"https://www.jiqizhixin.com/articles/abc123"}'],
      ['compose_briefing', '{"items":8}'],
    ]
    for (const [tool_name, args] of tools) {
      await sleep(450)
      push({ type: 'tool_call', tool_name, arguments: args })
      push({ type: 'tool_result', tool_name, arguments: args, result: 'ok', ok: true })
    }
    const id = db.seq++
    const content =
      '# 今日 AI 速览（mock）\n\n' +
      '- 这是离线假数据，真后端起来后这里会换成真实检索结果。'
    const title = '每日 AI 简报 · ' + new Date().toLocaleDateString('zh-CN')
    const items = [
      {
        title: '（示例）某开源模型发布新版本',
        url: 'https://www.jiqizhixin.com/articles/abc123',
        source: '机器之心',
        published_at: '2026-10-19T08:00:00Z',
        summary: '离线示例条目，非真实检索结果。',
        ocr: false,
        ocr_source_type: null,
        credibility: 'high',
      },
    ]
    push({ type: 'done', title, content, items, status: 'ok', review: '通过：mock 简报' })
    const briefing = {
      id,
      title,
      created_at: new Date().toISOString(),
      status: 'ok',
      query: 'AI agent 开源模型',
      content,
      items,
      steps: tools.map(([tool_name, args]) => ({ tool_name, arguments: args, result: 'ok', ok: true })),
      error: null,
    }
    db.briefings[id] = briefing
    if (db.users[userId]) db.users[userId].briefings.unshift(id)
    await sleep(300)
    push({ type: 'saved', briefing })
    return res.end()
  }

  return sendJson(res, 404, { code: 404, data: null, message: 'not found' })
})

async function start() {
  let mode = 'mock（离线假数据）'
  if (!MOCK_ONLY) {
    const alive = await probeUpstream()
    mode = alive ? `代理真实后端 ${UPSTREAM}` : 'mock（后端未启动，自动回落）'
  }
  server.listen(PORT, () => {
    console.log(`\n预览地址  : http://localhost:${PORT}`)
    console.log(`当前模式  : ${mode}`)
    console.log(`提示      : 后端起来后会自动切到代理模式，无需重启本服务\n`)
  })
}

start()
