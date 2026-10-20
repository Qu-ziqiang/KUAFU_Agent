// 前端逻辑单测：不需要浏览器，直接把 app.js 载入 VM 并注入 DOM 桩件。
// 用法：node unit-test.mjs
// 说明：app.js 是经典脚本，函数声明会挂到 VM 的全局，因此可直接取用测试；
//       app.js 本身不需要为测试做任何改动。

import vm from 'node:vm'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SRC = await readFile(join(__dirname, 'app.js'), 'utf8')

let pass = 0
let fail = 0
function ok(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✅ ${name}${detail ? ' — ' + detail : ''}`) }
  else { fail++; console.log(`  ❌ ${name}${detail ? ' — ' + detail : ''}`) }
}
const eq = (name, actual, expected) =>
  ok(name, JSON.stringify(actual) === JSON.stringify(expected), `得到 ${JSON.stringify(actual)} · 期望 ${JSON.stringify(expected)}`)

// ---------- DOM 桩件 ----------
function makeEl(id = '', sink = {}) {
  const base = {
    id, value: '', textContent: '', innerHTML: '', className: '', hidden: false,
    dataset: {}, disabled: false,
    addEventListener() {}, appendChild() {}, contains: () => false,
    querySelector: () => makeEl('sub'), querySelectorAll: () => [],
    classList: { toggle() {}, add() {}, remove() {} },
    focus() {}, blur() {}, closest: () => null,
  }
  return new Proxy(base, {
    get(t, p) {
      if (typeof p !== 'string') return t[p]
      if (p === 'then') return undefined // 防止被误判成 thenable
      if (p in t) return t[p]
      t[p] = makeEl(p, sink) // 未知属性 => 子元素桩（用于 form.name / form.recall_count 等）
      return t[p]
    },
    set(t, p, v) { t[p] = v; return true },
  })
}

// 载入 app.js，返回 VM 上下文（可访问其函数声明）
function loadApp(store = {}, fetchImpl = async () => ({ ok: true, json: async () => ({ code: 0, data: {} }) })) {
  const els = {}
  const el = (id) => (els[id] ||= makeEl(id))
  const ctx = vm.createContext({
    console,
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v) },
      removeItem: (k) => { delete store[k] },
    },
    document: {
      getElementById: el,
      querySelectorAll: () => [],
      activeElement: null,
    },
    fetch: fetchImpl,
    TextDecoder,
    setTimeout,
    encodeURIComponent,
  })
  vm.runInContext(SRC, ctx)
  return { ctx, el, store }
}

console.log('\n🧪 前端逻辑单测（app.js）\n')

// ---------- 1. splitList：中英文逗号拆分 ----------
console.log('[1] splitList 话题/关键词拆分')
{
  const { ctx } = loadApp()
  eq('英文逗号', ctx.splitList('a,b'), ['a', 'b'])
  eq('中文逗号', ctx.splitList('歌手，陶喆'), ['歌手', '陶喆'])
  eq('中英混用', ctx.splitList('开源模型, AI Agent，agent'), ['开源模型', 'AI Agent', 'agent'])
  eq('带空格会 trim', ctx.splitList(' a , b '), ['a', 'b'])
  eq('空串得空数组', ctx.splitList(''), [])
  eq('连续逗号不产生空项', ctx.splitList('a,,b,'), ['a', 'b'])
}

// ---------- 2. readLivePrefs：字段与夹取 ----------
console.log('\n[2] readLivePrefs 取值与 recall_count 夹取')
{
  const { ctx, el } = loadApp()
  const form = el('profile')
  form.topics.value = '歌手，演唱会'
  form.keywords.value = '陶喆'
  form.language.value = 'zh'
  form.recall_count.value = '5'
  form.custom_prompt.value = '多写演出和综艺，少写购票广告'

  const p = ctx.readLivePrefs()
  eq('topics 按中文逗号拆成数组', p.topics, ['歌手', '演唱会'])
  eq('keywords 拆成数组', p.keywords, ['陶喆'])
  eq('language', p.language, 'zh')
  eq('recall_count 正常值', p.recall_count, 5)

  // 上限与后端 RECALL_LIMIT = 30 保持一致
  form.recall_count.value = '0'
  eq('recall_count=0 夹到 1', ctx.readLivePrefs().recall_count, 1)
  form.recall_count.value = '-8'
  eq('recall_count=-8 夹到 1', ctx.readLivePrefs().recall_count, 1)
  form.recall_count.value = '30'
  eq('recall_count=30 保持 30', ctx.readLivePrefs().recall_count, 30)
  form.recall_count.value = '99'
  eq('recall_count=99 夹到 30', ctx.readLivePrefs().recall_count, 30)
  form.recall_count.value = '5.6'
  ok('recall_count=5.6 取整为 5（向下）', ctx.readLivePrefs().recall_count === 5, '前端按整数条数处理')
  form.recall_count.value = ''
  eq('recall_count 空值回落 5', ctx.readLivePrefs().recall_count, 5)
  form.recall_count.value = 'abc'
  eq('recall_count 非数字回落 5', ctx.readLivePrefs().recall_count, 5)
  form.recall_count.value = ' 7 '
  eq('recall_count 带空格也能解析', ctx.readLivePrefs().recall_count, 7)

  // custom_prompt 必须原样，不能被 trim 或改写
  form.recall_count.value = '5'
  form.custom_prompt.value = '  多写演出和综艺，少写购票广告  '
  ok('custom_prompt 原样保留首尾空格（不改写）', ctx.readLivePrefs().custom_prompt === '  多写演出和综艺，少写购票广告  ',
     JSON.stringify(ctx.readLivePrefs().custom_prompt))
  form.custom_prompt.value = ''
  ok('custom_prompt 空串原样', ctx.readLivePrefs().custom_prompt === '')
}

// ---------- 3. escapeHtml / renderMarkdown ----------
console.log('\n[3] 转义与 Markdown 渲染')
{
  const { ctx } = loadApp()
  ok('转义 <script>', ctx.escapeHtml('<script>').includes('&lt;script&gt;'))
  ok('转义 &', ctx.escapeHtml('a & b').includes('a &amp; b'))
  ok('转义 >', ctx.escapeHtml('a > b').includes('a &gt; b'))

  const md = ctx.renderMarkdown('# 标题\n- 要点\n\n正文')
  ok('渲染 h1', md.includes('<h1>标题</h1>'))
  ok('渲染列表', md.includes('• 要点'))
  ok('渲染段落', md.includes('<p>正文</p>'))

  const link = ctx.renderMarkdown('[原文](https://example.com/a)')
  ok('渲染外链并 target=_blank', link.includes('href="https://example.com/a"') && link.includes('target="_blank"'))
  ok('XSS 不被当作标签执行', !ctx.renderMarkdown('# <img onerror=alert(1)>').includes('<img'))
}

// ---------- 4. renderSources：来源卡片与 OCR/可信度徽章 ----------
console.log('\n[4] 来源卡片渲染')
{
  const { ctx, el } = loadApp()
  const items = [
    { title: '普通新闻', url: 'https://a.com/1', source: '机器之心', published_at: '2026-10-19T08:00:00Z', summary: '摘要', ocr: false, credibility: 'high' },
    { title: 'PDF 报告', url: 'https://b.com/x.pdf', source: '某机构', published_at: null, summary: 'OCR 正文', ocr: true, ocr_source_type: 'pdf', credibility: 'medium' },
  ]
  ctx.renderSources(items)
  const html = el('brief-sources').innerHTML
  ok('渲染两条来源', (html.match(/class="source-item"/g) || []).length === 2)
  ok('标题可点击到原始 url', html.includes('href="https://a.com/1"') && html.includes('class="source-title"'))
  ok('OCR 条目带 OCR 徽章', html.includes('badge ocr'))
  ok('含可信度徽章', html.includes('badge cred'))
  ok('打开原文按钮带 target=_blank', html.includes('rel="noreferrer"'))

  ctx.renderSources([])
  ok('无来源时显示占位', el('brief-sources').innerHTML.includes('暂无来源'))
}

// ---------- 5. 旧数字 id → UUID 迁移 ----------
console.log('\n[5] 旧数字 id 到 UUID 的迁移')
{
  const UUID = 'bd4877d9-bb03-4b5d-9037-2bb8efe92635'
  const store = { kuafu_user_id: '3' } // 模拟老用户存的是数字 id
  const fetchImpl = async (path) => {
    if (path.includes('/api/users/3/briefings')) return { ok: true, json: async () => ({ code: 0, data: [] }) }
    if (path.includes('/api/users/3')) {
      return {
        ok: true,
        json: async () => ({
          code: 0,
          data: {
            id: UUID, name: '林夏', email: 'x@y.com',
            preferences: { topics: ['AI'], keywords: ['agent'], language: 'zh', recall_count: 7, custom_prompt: '多写演出' },
          },
        }),
      }
    }
    return { ok: true, json: async () => ({ code: 0, data: {} }) }
  }
  const { ctx, el, store: s } = loadApp(store, fetchImpl)
  await ctx.loadUser()
  ok('本地 id 已被覆盖为 UUID', s.kuafu_user_id === UUID, s.kuafu_user_id)
  ok('覆盖的是字符串（非数字）', !/^\d+$/.test(String(s.kuafu_user_id)))

  // 同时验证偏好回填
  const form = el('profile')
  ok('回填 topics', form.topics.value === 'AI', form.topics.value)
  ok('回填 keywords', form.keywords.value === 'agent', form.keywords.value)
  ok('回填 recall_count', form.recall_count.value === 7, String(form.recall_count.value))
  ok('回填 custom_prompt', form.custom_prompt.value === '多写演出', form.custom_prompt.value)
}

// ---------- 6. 用户不存在时清理本地 id ----------
console.log('\n[6] 用户不存在时清理本地 id')
{
  const store = { kuafu_user_id: 'not-exist-id' }
  const fetchImpl = async () => ({ ok: true, json: async () => ({ code: 404, data: null, message: 'user not found' }) })
  const { store: s } = loadApp(store, fetchImpl)
  const { ctx } = loadApp(s, fetchImpl)
  await ctx.loadUser()
  ok('无效 id 被清除', !('kuafu_user_id' in s), JSON.stringify(s))
}

console.log(`\n通过 ${pass} · 失败 ${fail}`)
if (fail === 0) { console.log('✅ 全部通过\n'); process.exit(0) }
console.log('❌ 存在失败项\n')
process.exit(1)
