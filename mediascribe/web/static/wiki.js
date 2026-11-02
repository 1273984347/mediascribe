"use strict";
const $ = (id) => document.getElementById(id);

function authHeaders() {
  let t = "";
  try { t = (localStorage.getItem("v2t_api_token") || "").trim(); } catch (_) { /* storage 不可用 */ }
  return t ? { "Authorization": "Bearer " + t } : {};
}

// 2026-10-31 交互审查 P2-7: 按状态码给出**可执行**的中文提示。
function httpErrorText(status, statusText) {
  if (status === 401) return "未授权 — 请在转写台页面填入 API 令牌后重试";
  if (status === 403) return "令牌无效或来源被拒绝 — 请检查 API 令牌";
  if (status === 404) return "笔记不存在或已被删除";
  if (status === 429) return "请求过于频繁 — 请稍后再试";
  return `请求失败 (HTTP ${status}${statusText ? " " + statusText : ""})`;
}

async function readErrorText(resp) {
  try {
    const data = await resp.json();
    if (data && typeof data === "object" && typeof data.detail === "string") {
      return data.detail;
    }
  } catch (_) { /* 非 JSON 响应体 */ }
  return httpErrorText(resp.status, resp.statusText);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const KIND_LABEL = { raw: "转写", wiki: "聚合" };
const TYPE_ORDER = ["raw", "wiki"];

// name(stem) → note — wikilink 跳转 / 时间戳深链的解析表 (Phase 4)
const notesByName = new Map();

function noteItem(n) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "note-item";
  btn.dataset.name = n.name;
  btn.innerHTML = `<span class="kind">${n.type === "raw" ? "RAW" : "WIKI"}</span><span class="n"></span>`;
  btn.querySelector(".n").textContent = n.name;
  btn.addEventListener("click", () => openNote(n, btn));
  return btn;
}

let current = null; // { note, text }

// 线性 SVG 图标(1.5 描边 / 圆角, 跟随主题色) — 与 KIMI 图标语言一致
const ICONS = {
  copy:
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  check:
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
  trash:
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>',
  render:
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
  raw:
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/></svg>',
};

// ---------------------------------------------------------------------------
// Phase 4: 渲染态阅读 + wikilink 跳转 + 时间戳深链
// ---------------------------------------------------------------------------
// 安全策略: 文本先整体 escapeHtml, 再做模式变换 — 用户内容永远不会以
// 原始 HTML 进入 DOM。外链只放行 http(s); 内部跳转走 JS 委托, 不拼 href。

function tsToSeconds(ts) {
  const parts = ts.split(":").map((x) => parseInt(x, 10));
  if (parts.some((n) => Number.isNaN(n)) || parts.length < 2 || parts.length > 3) return null;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

// 平台时间戳深链: YouTube/B站支持 t=秒; 其它平台退化为原样打开。
function withTimestamp(url, seconds) {
  if (!url || !Number.isFinite(seconds)) return url || "";
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}t=${seconds}s`;
}

function safeHref(url) {
  const u = (url || "").trim();
  return /^https?:\/\//i.test(u) ? u : "";
}

/** 单行内联变换 — 输入已转义。 */
function renderInline(line, sourceUrl) {
  let out = line;
  // [text](url) — 只放行 http(s)
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, label, href) => {
    return `<a class="ext" href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });
  // [[wikilink]] — 内部跳转 (data-name 由 escapeHtml 后的文本再反转义不可行,
  // 因此对 name 单独转义: 此处文本已转义, name 取自转义串, 安全)
  out = out.replace(/\[\[([^\]]+)\]\]/g, (_, name) => {
    const exists = notesByName.has(name);
    const cls = exists ? "wiki-link" : "wiki-link missing";
    const tip = exists ? `打开「${name}」` : "笔记不存在";
    return `<span class="${cls}" data-wikilink="${name}" title="${tip}">${name}</span>`;
  });
  // **[mm:ss]** / **[h:mm:ss]** — 时间戳深链
  out = out.replace(/\*\*\[(\d{1,3}:[0-5]?\d(?::[0-5]?\d)?)\]\*\*/g, (_, ts) => {
    const sec = tsToSeconds(ts);
    const href = sourceUrl ? withTimestamp(sourceUrl, sec) : "";
    if (!href) return `<span class="ts-stamp" title="无来源链接">${ts}</span>`;
    return `<a class="ts-link" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer" title="在来源视频中定位 ${ts}">${ts}</a>`;
  });
  // **bold** / `code`
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  return out;
}

/** 渲染整篇笔记 → HTML 字符串 (输入为原文, 内部先转义)。 */
function renderMarkdown(text, note) {
  // 去掉 frontmatter (raw 笔记的元数据块 — 渲染态阅读不需要);
  // frontmatter 里的 url 作为来源兜底 (索引表缺失时仍能深链)
  let body = text;
  let fmUrl = "";
  if (body.startsWith("---")) {
    const end = body.indexOf("\n---", 3);
    if (end >= 0) {
      const m = body.slice(0, end).match(/^url:\s*(.+)$/m);
      if (m) fmUrl = m[1].trim().replace(/^"|"$/g, "");
      body = body.slice(end + 4);
    }
  }
  const lines = body.split("\n");
  // 概念笔记里时间戳挂在 "## [[raw 名]]" 小节下 — 追踪最近的小节来源
  let sourceUrl =
    note.type === "raw" ? notesByName.get(note.name)?.url || fmUrl : fmUrl;
  const out = [];
  let inList = false;
  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };

  for (const raw0 of lines) {
    const headingSrc = raw0.match(/^##\s+\[\[([^\]]+)\]\]/);
    if (headingSrc) {
      const src = notesByName.get(headingSrc[1]);
      sourceUrl = src?.url || "";
    }
    const raw = escapeHtml(raw0);
    const trimmed = raw.trim();
    if (!trimmed) { closeList(); continue; }
    const h = trimmed.match(/^(#{1,3})\s+(.*)$/);
    if (h) {
      closeList();
      const level = h[1].length;
      out.push(`<h${level}>${renderInline(h[2], sourceUrl)}</h${level}>`);
      continue;
    }
    if (trimmed.startsWith("&gt;")) {
      closeList();
      out.push(`<blockquote>${renderInline(trimmed.slice(4).trim(), sourceUrl)}</blockquote>`);
      continue;
    }
    if (/^[-*]\s+/.test(trimmed)) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${renderInline(trimmed.replace(/^[-*]\s+/, ""), sourceUrl)}</li>`);
      continue;
    }
    closeList();
    out.push(`<p>${renderInline(trimmed, sourceUrl)}</p>`);
  }
  closeList();
  return out.join("\n");
}

/** wikilink 点击 — 事件委托 (渲染区重建后仍有效)。 */
function handleWikiLinkClick(ev) {
  const el = ev.target.closest("[data-wikilink]");
  if (!el) return false;
  const name = el.getAttribute("data-wikilink");
  const note = notesByName.get(name);
  if (!note) return false;
  const btn = document.querySelector(`.note-item[data-name="${CSS.escape(name)}"]`);
  openNote(note, btn);
  return true;
}

function bindViewerClicks() {
  const viewer = $("viewer");
  viewer.addEventListener("click", (ev) => {
    if (handleWikiLinkClick(ev)) return;
    const copyBtn = ev.target.closest("[data-copy]");
    if (copyBtn) { copyNote.call(copyBtn, { currentTarget: copyBtn }); return; }
    const delBtn = ev.target.closest("[data-del]");
    if (delBtn) { deleteNote.call(delBtn, { currentTarget: delBtn }); }
  });
}

let viewMode = "rendered"; // "rendered" | "raw"

function viewerBodyHtml(note, text) {
  if (viewMode === "raw") {
    return `<pre></pre>`;
  }
  return `<div class="rendered">${renderMarkdown(text, note)}</div>`;
}

function applyViewMode() {
  if (!current) return;
  const body = $("viewer").querySelector("[data-body]");
  if (!body) return;
  body.innerHTML = viewerBodyHtml(current.note, current.text);
  if (viewMode === "raw") body.querySelector("pre").textContent = current.text;
  const tgl = $("viewer").querySelectorAll("[data-view]");
  tgl.forEach((b) => b.classList.toggle("on", b.dataset.view === viewMode));
}

async function openNote(note, btn) {
  document.querySelectorAll(".note-item.active").forEach((el) => el.classList.remove("active"));
  if (btn) btn.classList.add("active");
  $("viewer").innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await fetch(`/api/wiki/note?name=${encodeURIComponent(note.path)}`, { headers: authHeaders() });
    if (!r.ok) throw new Error(await readErrorText(r));
    const text = await r.text();
    current = { note, text };
    $("viewer").innerHTML = `
      <div class="title">
        <span class="name"></span>
        <span class="grp">
          <span class="kind">${note.type === "raw" ? "RAW" : "WIKI"}</span>
          <button type="button" class="act viewtoggle on" data-view="rendered" title="渲染态阅读">${ICONS.render}</button>
          <button type="button" class="act viewtoggle" data-view="raw" title="查看原文">${ICONS.raw}</button>
          <button type="button" class="act" data-copy title="复制全文">${ICONS.copy}</button>
          <button type="button" class="act" data-del title="删除这篇笔记">${ICONS.trash}</button>
        </span>
      </div>
      <div data-body></div>`;
    $("viewer").querySelector(".name").textContent = note.name;
    applyViewMode();
  } catch (err) {
    current = null;
    // err.message 可能来自响应 statusText — 走 textContent 防注入
    setViewerMessage("加载失败:" + err.message, "err");
  }
}

async function copyNote(ev) {
  if (!current) return;
  const b = ev.currentTarget;
  try {
    await navigator.clipboard.writeText(current.text);
    b.innerHTML = ICONS.check;
    b.classList.add("done");
    b.title = "已复制";
    setTimeout(() => {
      b.innerHTML = ICONS.copy;
      b.classList.remove("done");
      b.title = "复制全文";
    }, 1500);
  } catch (_) {
    b.title = "复制失败:浏览器拒绝了剪贴板访问";
  }
}

async function deleteNote(ev) {
  const b = ev.currentTarget;
  // 没有选中笔记时不该走到这里 —— 2026-10-31 交互审查:
  // 旧代码直接 `current.note.path`, current 为 null 时抛 TypeError,
  // 而 catch 又只写 b.title ⇒ 用户点了没反应, 也没错误提示。
  if (!current) {
    setViewerMessage("请先从左侧选择一篇笔记。", "err");
    return;
  }
  // 两步确认: 第一次点变红, 3 秒内再点才真正删除
  if (!b.classList.contains("armed")) {
    b.classList.add("armed");
    b.title = "再点一次确认删除";
    setTimeout(() => { b.classList.remove("armed"); b.title = "删除这篇笔记"; }, 3000);
    return;
  }
  b.classList.remove("armed");
  try {
    const r = await fetch(`/api/wiki/note?name=${encodeURIComponent(current.note.path)}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error(await readErrorText(r));
    current = null;
    $("viewer").innerHTML = '<div class="empty">已删除。</div>';
    load();
  } catch (err) {
    // 错误必须**看得见** —— 只写 title 等于没提示。
    setViewerMessage(`删除失败:${err.message}`, "err");
  }
}

// 在查看区显示一条消息。
//
// 2026-10-31 独立盲审 P2-7: 错误消息**追加**在正文下方, 正文保留;
// 只有"还没有打开任何笔记"时才整区替换。
function setViewerMessage(text, kind) {
  const viewer = $("viewer");
  const box = document.createElement("div");
  box.className = kind === "err" ? "empty viewer-err" : "empty";
  box.textContent = text;
  if (kind === "err" && viewer.querySelector("[data-body]")) {
    viewer.appendChild(box);
    return;
  }
  viewer.replaceChildren(box);
}

// ---------------------------------------------------------------------------
// Phase 4: 全文搜索 (服务端 /api/wiki/search, 空查询恢复普通列表)
// ---------------------------------------------------------------------------
let searchSeq = 0;

async function runSearch(q) {
  const seq = ++searchSeq;
  const list = $("note-list");
  if (!q.trim()) { load(); return; }
  list.innerHTML = '<div class="empty">搜索中…</div>';
  try {
    const r = await fetch(`/api/wiki/search?q=${encodeURIComponent(q)}`, { headers: authHeaders() });
    if (!r.ok) throw new Error(await readErrorText(r));
    const data = await r.json();
    if (seq !== searchSeq) return; // 已有更新的查询, 丢弃过期结果
    list.innerHTML = "";
    const results = data.results || [];
    const h = document.createElement("h2");
    h.className = "sec";
    h.textContent = `搜索「${q}」— ${results.length} 条`;
    list.appendChild(h);
    if (results.length === 0) {
      list.insertAdjacentHTML("beforeend", '<div class="empty">没有匹配的笔记</div>');
      return;
    }
    const box = document.createElement("div");
    box.className = "note-list";
    for (const hit of results) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-item search-hit";
      btn.dataset.name = hit.name;
      btn.innerHTML = `<span class="kind">${hit.type === "raw" ? "RAW" : "WIKI"}</span><span class="n"></span><span class="snippet"></span>`;
      btn.querySelector(".n").textContent = hit.name;
      btn.querySelector(".snippet").textContent = hit.snippet || "";
      btn.addEventListener("click", () => openNote(hit, btn));
      box.appendChild(btn);
    }
    list.appendChild(box);
  } catch (err) {
    list.innerHTML = "";
    const box = document.createElement("div");
    box.className = "empty viewer-err";
    box.textContent = "搜索失败:" + err.message;
    list.appendChild(box);
  }
}

async function load() {
  const list = $("note-list");
  list.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await fetch("/api/wiki", { headers: authHeaders() });
    if (!r.ok) throw new Error(await readErrorText(r));
    const data = await r.json();
    if (!data.enabled) {
      list.innerHTML = '<div class="empty">知识库未启用(设置 MEDIASCRIBE_WIKI=1)</div>';
      return;
    }
    $("vault-path").textContent = data.path || "";
    notesByName.clear();
    const notes = data.notes || [];
    notes.forEach((n) => notesByName.set(n.name, n));
    list.innerHTML = "";
    if (notes.length === 0) {
      list.innerHTML = '<div class="empty">还没有归档笔记 — 提交一次转写后这里就会出现内容</div>';
      return;
    }
    for (const type of TYPE_ORDER) {
      const group = notes.filter((n) => n.type === type);
      if (group.length === 0) continue;
      const h = document.createElement("h2");
      h.className = "sec";
      h.textContent = type === "raw" ? `转写笔记 (${group.length})` : `聚合笔记 (${group.length})`;
      list.appendChild(h);
      const box = document.createElement("div");
      box.className = "note-list";
      group.forEach((n) => box.appendChild(noteItem(n)));
      list.appendChild(box);
    }
  } catch (err) {
    // err.message 可能来自响应 statusText — 走 textContent 防注入
    const box = document.createElement("div");
    box.className = "empty";
    box.textContent = "加载失败:" + err.message;
    list.replaceChildren(box);
  }
}

bindViewerClicks();

// 搜索框: 250ms 防抖, 空查询恢复列表
const searchInput = $("wiki-search");
if (searchInput) {
  let timer = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(timer);
    const q = searchInput.value;
    timer = setTimeout(() => runSearch(q), 250);
  });
}

load();

// 导出供 node 冒烟测试 (浏览器 module script 里无害)
export { escapeHtml, tsToSeconds, withTimestamp, renderMarkdown, renderInline };
