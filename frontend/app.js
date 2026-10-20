const STORAGE_KEY = "kuafu_user_id";

const state = {
  // 用户 id 现在是 UUID 字符串，不再当数字处理
  userId: localStorage.getItem(STORAGE_KEY) || "",
  activeId: null,
  liveItems: [],
  edited: false,
};

const $ = (id) => document.getElementById(id);
const form = $("profile");

const STATUS_TEXT = { ok: "已完成", partial: "部分完成", failed: "生成失败" };

const TOOL_HINT = {
  web_search: "正在联网检索",
  fetch_webpage: "正在抓取网页正文",
  ocr_document: "正在对文档做 OCR",
  trace_source: "正在核验来源",
  compose_briefing: "正在汇总成稿",
};

function splitList(value) {
  return value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
}

function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text || "";
  el.className = "status" + (kind ? ` is-${kind}` : "");
}

function setRunning(running) {
  const btn = $("run");
  btn.disabled = running;
  btn.querySelector(".btn-label").textContent = running ? "正在检索…" : "生成今日简报";
  btn.querySelector(".spinner").hidden = !running;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json();
  if (!response.ok || body.code !== 0) {
    throw new Error(body.message || "请求失败");
  }
  return body.data;
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function keywordTerms() {
  return [...splitList(form.topics.value), ...splitList(form.keywords.value)]
    .map((item) => item.trim())
    .filter((item) => item.length >= 2)
    .sort((a, b) => b.length - a.length);
}

function emphasizeTerms(text) {
  const terms = keywordTerms();
  if (!terms.length || !text) return text;
  const pattern = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  return String(text)
    .split(/(\*\*.*?\*\*)/g)
    .map((part) => {
      if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) return part;
      return part.replace(new RegExp(pattern, "gi"), (match) => `**${match}**`);
    })
    .join("");
}

function renderMarkdown(markdown) {
  const html = [];
  for (const raw of String(markdown || "").split("\n")) {
    if (raw.startsWith("- ")) {
      const body = raw.slice(2);
      const splitAt = body.indexOf("：");
      if (splitAt > 0) {
        const title = escapeHtml(body.slice(0, splitAt).replace(/\*\*/g, ""));
        const summary = inline(escapeHtml(emphasizeTerms(body.slice(splitAt + 1))));
        html.push(`<p>• <strong>${title}</strong>：${summary}</p>`);
        continue;
      }
    }
    const line = escapeHtml(emphasizeTerms(raw));
    if (line.startsWith("# ")) html.push(`<h1>${inline(line.slice(2))}</h1>`);
    else if (line.startsWith("## ")) html.push(`<h2>${inline(line.slice(3))}</h2>`);
    else if (line.startsWith("### ")) html.push(`<h3>${inline(line.slice(4))}</h3>`);
    else if (line.startsWith("- ")) html.push(`<p>• ${inline(line.slice(2))}</p>`);
    else if (line.trim() === "") html.push("");
    else html.push(`<p>${inline(line)}</p>`);
  }
  return html.join("");
}

function inline(text) {
  const bold = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*/g, "");
  const linked = bold.replace(
    /\[(.+?)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer">$1</a>',
  );
  return linked.replace(
    /(^|[^"'=])(https?:\/\/[^\s<]+)/g,
    '$1<a href="$2" target="_blank" rel="noreferrer">$2</a>',
  );
}

function showBriefing(briefing) {
  state.activeId = briefing.id;
  $("brief-title").textContent = briefing.title || "简报";

  const pill = $("brief-status");
  if (briefing.status) {
    pill.hidden = false;
    pill.textContent = STATUS_TEXT[briefing.status] || briefing.status;
    pill.className = "pill" + (briefing.status !== "ok" ? ` ${briefing.status}` : "");
  } else {
    pill.hidden = true;
  }

  const when = briefing.created_at ? new Date(briefing.created_at).toLocaleString("zh-CN") : "";
  $("brief-meta").textContent = [when, briefing.query ? `检索词：${briefing.query}` : ""].filter(Boolean).join("　·　");

  const body = $("brief-body");
  body.className = "body";
  body.innerHTML = renderMarkdown(briefing.content || briefing.error || "");

  renderSources(briefing.items || []);
  highlightHistory();
}

function renderSources(items) {
  const box = $("brief-sources");
  if (!items.length) {
    box.innerHTML = '<li class="meta">本期暂无来源条目。</li>';
    return;
  }
  box.innerHTML = items.map((item) => {
    const url = escapeHtml(item.url || "");
    const title = escapeHtml(String(item.title || url || "来源").replace(/\*+/g, ""));
    const source = escapeHtml(item.source || "");
    const when = item.published_at ? new Date(item.published_at).toLocaleDateString("zh-CN") : "";
    const ocr = item.ocr ? '<span class="badge ocr">OCR</span>' : "";
    const credClass = item.credibility === "medium" || item.credibility === "low" ? ` ${item.credibility}` : "";
    const cred = item.credibility
      ? `<span class="badge cred${credClass}">可信度 ${escapeHtml(item.credibility)}</span>`
      : "";
    const summary = escapeHtml(item.summary || "");
    const link = url
      ? `<a href="${url}" target="_blank" rel="noreferrer" class="source-link">↗ 查看原始链接</a>`
      : "";
    return `<li class="source-item">
      <a href="${url}" target="_blank" rel="noreferrer" class="source-title">${title}</a>
      <div class="source-meta">${source ? `来源：${source}` : ""}${when ? ` · ${when}` : ""}${ocr}${cred}</div>
      ${summary ? `<p class="source-summary">${summary}</p>` : ""}
      ${link}
    </li>`;
  }).join("");
}

function renderHistory(items) {
  const box = $("history");
  if (!items.length) {
    box.innerHTML = "<li class='meta'>还没有历史简报</li>";
    return;
  }
  box.innerHTML = items.map((item) => {
    const when = item.created_at ? new Date(item.created_at).toLocaleString("zh-CN") : "";
    const count = item.item_count ? ` · ${item.item_count} 条` : "";
    return `<li><button type="button" data-id="${item.id}">${escapeHtml(item.title)}<span class="meta">${escapeHtml(when)}${count}</span></button></li>`;
  }).join("");
  highlightHistory();
}

function highlightHistory() {
  document.querySelectorAll("#history button[data-id]").forEach((btn) => {
    btn.classList.toggle("active", String(btn.dataset.id) === String(state.activeId));
  });
}

async function loadHistory() {
  if (!state.userId) return;
  const items = await api(`/api/users/${state.userId}/briefings`);
  renderHistory(items);
}

async function loadUser() {
  if (!state.userId) return;
  try {
    const user = await api(`/api/users/${encodeURIComponent(state.userId)}`);
    // 旧的数字 id 仍可被 GET 到，但响应里的 id 已是新 UUID —— 拿到立刻覆盖本地保存
    const serverId = user.id != null ? String(user.id) : "";
    if (serverId && serverId !== String(state.userId)) {
      state.userId = serverId;
      localStorage.setItem(STORAGE_KEY, serverId);
    }
    if (!state.edited) {
      form.name.value = user.name || "";
      form.email.value = user.email || "";
      form.topics.value = (user.preferences.topics || []).join(", ");
      form.keywords.value = (user.preferences.keywords || []).join(", ");
      form.language.value = user.preferences.language || "zh";
      form.recall_count.value = user.preferences.recall_count || 5;
      form.custom_prompt.value = user.preferences.custom_prompt || "仅仅输出国内的资讯";
    }
    await loadHistory();
  } catch {
    state.userId = "";
    localStorage.removeItem(STORAGE_KEY);
  }
}

form.addEventListener("input", () => {
  state.edited = true;
});

function readLivePrefs() {
  const active = document.activeElement;
  if (active && form.contains(active)) active.blur();
  const raw = String(form.recall_count.value || "").trim();
  // 与后端 RECALL_LIMIT = 30 对齐；后端 schema 里是 int，这里也先取整再夹取
  const recall = Number.isFinite(Number(raw)) && raw !== "" ? Math.trunc(Number(raw)) : NaN;
  const prefs = {
    topics: splitList(form.topics.value),
    keywords: splitList(form.keywords.value),
    language: form.language.value || "zh",
    recall_count: Number.isFinite(recall) ? Math.min(30, Math.max(1, recall)) : 5,
    custom_prompt: form.custom_prompt.value, // 原样提交，前端不改写
  };
  // 生成时也会保存：空框补回默认值，避免条数看起来“没带上”
  form.recall_count.value = String(prefs.recall_count);
  return prefs;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("正在保存…");
  try {
    if (!state.userId) {
      const created = await api("/api/users", {
        method: "POST",
        body: JSON.stringify({ name: form.name.value.trim(), email: form.email.value.trim() || null }),
      });
      state.userId = created.id;
      localStorage.setItem(STORAGE_KEY, String(created.id));
    }
    await api(`/api/users/${state.userId}/preferences`, {
      method: "PUT",
      body: JSON.stringify(readLivePrefs()),
    });
    setStatus("偏好已保存", "done");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

async function ensureUserAndPreferences() {
  const name = form.name.value.trim();
  if (!name) {
    throw new Error("请先填写称呼");
  }
  if (!state.userId) {
    const created = await api("/api/users", {
      method: "POST",
      body: JSON.stringify({ name, email: form.email.value.trim() || null }),
    });
    state.userId = created.id;
    localStorage.setItem("kuafu_user_id", String(created.id));
  }
  const prefs = readLivePrefs();
  await api(`/api/users/${state.userId}/preferences`, {
    method: "PUT",
    body: JSON.stringify(prefs),
  });
  return prefs;
}

$("run").addEventListener("click", async () => {
  setRunning(true);
  state.liveItems = [];
  $("brief-title").textContent = "正在生成今日简报…";
  $("brief-status").hidden = false;
  $("brief-status").textContent = "生成中";
  $("brief-status").className = "pill partial";
  $("brief-body").className = "body";
  $("brief-body").innerHTML = "<p>正在联网检索，有结果会马上出现在下方。</p>";
  renderSources([]);
  setStatus("正在联网检索…");
  try {
    const prefs = await ensureUserAndPreferences();
    const response = await fetch(`/api/users/${state.userId}/briefings/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(prefs),
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const event = JSON.parse(line.slice(6));
        if (event.type === "status" && event.message) {
          setStatus(event.message);
        }
        if (event.type === "tool_result") {
          setStatus(`${TOOL_HINT[event.tool_name] || event.tool_name}…`);
        }
        if (event.type === "item_draft" && event.item && event.phase === "summary") {
          const idx = state.liveItems.findIndex((item) => item.url === event.item.url);
          if (idx >= 0) state.liveItems[idx] = { ...state.liveItems[idx], ...event.item };
          else state.liveItems.push(event.item);
          renderSources(state.liveItems);
          setStatus(`已整理第 ${event.index || state.liveItems.length} 条…`);
        }
        // 只接受稳定更新：正文可先显示进度；引用来源只增不减。
        if (event.type === "sources_update" && event.stable) {
          const next = event.items || [];
          if (next.length >= state.liveItems.length) {
            state.liveItems = next;
            renderSources(state.liveItems);
          }
          if (event.content) {
            $("brief-title").textContent = event.title || "每日 AI 简报（生成中）";
            $("brief-body").className = "body";
            $("brief-body").innerHTML = renderMarkdown(event.content);
          }
        }
        if (event.type === "first_ready") {
          const items = event.items || state.liveItems;
          if ((items || []).length >= state.liveItems.length) {
            state.liveItems = items;
          }
          showBriefing({
            title: event.title,
            content: event.content,
            items: state.liveItems,
            query: event.query,
            status: "partial",
            created_at: new Date().toISOString(),
          });
          const count = state.liveItems.length;
          if (event.shortfall) {
            setStatus(event.shortfall, "error");
          } else {
            setStatus(
              event.recall_complete
                ? `已整理 ${count} 条，正在保存…`
                : `已写出 ${count} 条（约 ${event.elapsed_seconds || "?"} 秒），继续整理…`,
            );
          }
        }
        if (event.type === "done" && event.shortfall) {
          setStatus(event.shortfall, "error");
        }
        if (event.type === "error") {
          finished = true;
          setStatus(event.message, "error");
        }
        if (event.type === "saved") {
          finished = true;
          showBriefing(event.briefing);
          const want = Number(form.recall_count.value) || 0;
          const got = (event.briefing.items || []).length;
          if (want > 0 && got < want) {
            setStatus(`目标 ${want} 条，实际找到 ${got} 条相关资讯（相关不够，未凑数）`, "error");
          } else {
            setStatus("简报已生成", "done");
          }
          await loadHistory();
        }
      }
    }
    if (!finished) setStatus("生成中断了，请再点一次", "error");
  } catch (error) {
    setStatus(error.message || "生成失败", "error");
  } finally {
    setRunning(false);
  }
});

$("history").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-id]");
  if (!button) return;
  const briefing = await api(`/api/briefings/${button.dataset.id}`);
  showBriefing(briefing);
});

loadUser();
