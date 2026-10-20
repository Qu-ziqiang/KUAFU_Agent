// 接口冒烟测试：跑完整链路并断言，失败以非 0 退出（可直接用于回归）。
// 用法：node smoke-test.mjs
// 可选：PREVIEW_URL=http://127.0.0.1:5173  TIMEOUT_MS=180000
// 默认打向预览服务 5173（它就是浏览器实际访问的地址，会代理到真后端）。

const BASE = process.env.PREVIEW_URL || 'http://127.0.0.1:5173'
const TIMEOUT_MS = Number(process.env.TIMEOUT_MS || 180000)

let pass = 0
let fail = 0
let warn = 0

function record(kind, name, detail = '') {
  if (kind === 'pass') { pass++; console.log(`  ✅ ${name}${detail ? ' — ' + detail : ''}`) }
  else if (kind === 'warn') { warn++; console.log(`  ⚠️  ${name}${detail ? ' — ' + detail : ''}`) }
  else { fail++; console.log(`  ❌ ${name}${detail ? ' — ' + detail : ''}`) }
}
const check = (name, cond, detail = '') => record(cond ? 'pass' : 'fail', name, detail)
const warnOf = (name, detail = '') => record('warn', name, detail)

async function getJson(path, timeout = 15000) {
  const res = await fetch(BASE + path, { signal: AbortSignal.timeout(timeout) })
  const body = await res.json()
  return { res, body }
}

async function postJson(path, payload, timeout = 15000) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(timeout),
  })
  return { res, body: await res.json() }
}

async function putJson(path, payload, timeout = 15000) {
  const res = await fetch(BASE + path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(timeout),
  })
  return { res, body: await res.json() }
}

const PREFS = {
  topics: ['开源模型', 'AI Agent'],
  keywords: ['agent', 'RAG'],
  language: 'zh',
  recall_count: 5,
  custom_prompt: '多写演出和综艺，少写购票广告',
}

async function main() {
  const t0 = Date.now()
  console.log(`\n🔍 接口冒烟测试 · ${BASE}\n`)

  // 1. 静态页
  console.log('[1] 静态页面')
  {
    const res = await fetch(BASE + '/', { signal: AbortSignal.timeout(10000) })
    const html = await res.text()
    check('页面 200', res.status === 200, `HTTP ${res.status}`)
    check('标题为「每日 AI 新闻助手」', html.includes('每日 AI 新闻助手'))
    check('命名输入 recall_count 存在', html.includes('name="recall_count"'))
    check('命名输入 custom_prompt 存在', html.includes('name="custom_prompt"'))
  }

  // 2. 健康检查
  console.log('\n[2] 健康检查')
  {
    const { res, body } = await getJson('/api/health')
    check('HTTP 200', res.status === 200)
    check('code === 0', body?.code === 0)
    const d = body?.data || {}
    check('llm_ready', d.llm_ready === true)
    check('search_ready', d.search_ready === true)
    if (String(d.model || '').includes('mock')) {
      warnOf('当前是 mock 数据，不是真后端', `model=${d.model}`)
    } else {
      record('pass', '命中真后端', `model=${d.model} / ${d.search_backend}`)
    }
  }

  // 3. 创建用户（UUID）
  console.log('\n[3] 创建用户')
  let userId = ''
  {
    const { res, body } = await postJson('/api/users', { name: '冒烟测试', email: null })
    check('HTTP 200', res.status === 200)
    check('code === 0', body?.code === 0)
    userId = String(body?.data?.id ?? '')
    check('返回 id', !!userId, userId)
    check('id 是 UUID 字符串（非纯数字）', !/^\d+$/.test(userId), userId)
  }
  if (!userId) { summary(); return }

  // 4. 偏好写入 + 回填
  console.log('\n[4] 偏好写入与回填')
  {
    const { res, body } = await putJson(`/api/users/${userId}/preferences`, PREFS)
    check('PUT HTTP 200', res.status === 200)
    check('PUT code === 0', body?.code === 0)

    const { body: got } = await getJson(`/api/users/${userId}`)
    const p = got?.data?.preferences || {}
    check('topics 往返一致', JSON.stringify(p.topics) === JSON.stringify(PREFS.topics), JSON.stringify(p.topics))
    check('keywords 往返一致', JSON.stringify(p.keywords) === JSON.stringify(PREFS.keywords), JSON.stringify(p.keywords))
    check('recall_count 往返一致', p.recall_count === PREFS.recall_count, String(p.recall_count))
    check('custom_prompt 原样往返（未被改写）', p.custom_prompt === PREFS.custom_prompt, JSON.stringify(p.custom_prompt))
  }

  // 5. SSE 流式生成
  console.log('\n[5] SSE 流式生成')
  let briefing = null
  {
    const t = Date.now()
    const res = await fetch(`${BASE}/api/users/${userId}/briefings/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(PREFS),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    })
    check('SSE HTTP 200', res.status === 200)
    check('Content-Type 为 text/event-stream', (res.headers.get('content-type') || '').includes('text/event-stream'), res.headers.get('content-type'))

    const events = []
    const tools = new Set()
    const decoder = new TextDecoder()
    let buffer = ''
    for await (const chunk of res.body) {
      buffer += decoder.decode(chunk, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        const line = part.split('\n').find((l) => l.startsWith('data: '))
        if (!line) continue
        try {
          const ev = JSON.parse(line.slice(6))
          events.push(ev)
          if (ev.tool_name) tools.add(ev.tool_name)
        } catch { /* 忽略非 JSON 心跳 */ }
      }
    }
    const elapsed = ((Date.now() - t) / 1000).toFixed(1)
    check('收到 SSE 事件', events.length > 0, `${events.length} 个 / ${elapsed}s`)
    check('含 start 事件', events.some((e) => e.type === 'start'))
    check('含 tool_result 事件', events.some((e) => e.type === 'tool_result'), `${tools.size} 种工具`)
    check('含 saved 事件', events.some((e) => e.type === 'saved'), `工具：${[...tools].join(' → ') || '无'}`)
    const saved = events.find((e) => e.type === 'saved')
    briefing = saved?.briefing || null
    if (briefing) {
      check('saved 带 briefing.id', !!briefing.id, String(briefing.id))
      check('saved 带 items', Array.isArray(briefing.items), `${(briefing.items || []).length} 条`)
    } else {
      check('saved 带 briefing', false, '未收到 saved')
    }
  }

  // 6. 详情与列表
  if (briefing?.id) {
    console.log('\n[6] 详情与历史列表')
    const { body } = await getJson(`/api/briefings/${briefing.id}`)
    const d = body?.data || {}
    check('详情 code === 0', body?.code === 0)
    check('status 为 ok/partial/failed', ['ok', 'partial', 'failed'].includes(d.status), d.status)
    check('items 非空', (d.items || []).length > 0, `${(d.items || []).length} 条`)
    const withUrl = (d.items || []).filter((i) => !!i.url).length
    check('每条来源都有 url（可溯源）', withUrl === (d.items || []).length, `${withUrl}/${(d.items || []).length}`)

    const { body: list } = await getJson(`/api/users/${userId}/briefings`)
    check('列表 data 是数组', Array.isArray(list?.data))
    check('新简报出现在列表中', (list?.data || []).some((b) => String(b.id) === String(briefing.id)))
  }

  console.log(`\n总耗时 ${((Date.now() - t0) / 1000).toFixed(1)}s`)
  summary()
}

function summary() {
  console.log(`\n────────────────────────────`)
  console.log(`通过 ${pass} · 警告 ${warn} · 失败 ${fail}`)
  if (fail === 0) { console.log('✅ 全部通过\n'); process.exit(0) }
  console.log('❌ 存在失败项\n')
  process.exit(1)
}

main().catch((err) => {
  console.error(`\n💥 测试异常终止：${err?.message || err}\n`)
  process.exit(1)
})
