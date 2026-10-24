"use strict";
const $ = (id) => document.getElementById(id);

function authHeaders() {
  let t = "";
  try { t = (localStorage.getItem("v2t_api_token") || "").trim(); } catch (_) { /* storage 不可用 */ }
  return t ? { "Authorization": "Bearer " + t } : {};
}

const KIND_LABEL = { raw: "转写", wiki: "聚合" };
const TYPE_ORDER = ["raw", "wiki"];

function noteItem(n) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "note-item";
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
};

async function openNote(note, btn) {
  document.querySelectorAll(".note-item.active").forEach((el) => el.classList.remove("active"));
  if (btn) btn.classList.add("active");
  $("viewer").innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await fetch(`/api/wiki/note?name=${encodeURIComponent(note.path)}`, { headers: authHeaders() });
    if (!r.ok) throw new Error(r.statusText);
    const text = await r.text();
    current = { note, text };
    $("viewer").innerHTML = `
      <div class="title">
        <span class="name"></span>
        <span class="grp">
          <span class="kind">${note.type === "raw" ? "RAW" : "WIKI"}</span>
          <button type="button" class="act" data-copy title="复制全文">${ICONS.copy}</button>
          <button type="button" class="act" data-del title="删除这篇笔记">${ICONS.trash}</button>
        </span>
      </div>
      <pre></pre>`;
    $("viewer").querySelector(".name").textContent = note.name;
    $("viewer").querySelector("pre").textContent = text;
    $("viewer").querySelector("[data-copy]").addEventListener("click", copyNote);
    $("viewer").querySelector("[data-del]").addEventListener("click", deleteNote);
  } catch (err) {
    current = null;
    // err.message 可能来自响应 statusText — 走 textContent 防注入
    const box = document.createElement("div");
    box.className = "empty";
    box.textContent = "加载失败:" + err.message;
    $("viewer").replaceChildren(box);
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
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }
    current = null;
    $("viewer").innerHTML = '<div class="empty">已删除。</div>';
    load();
  } catch (err) {
    b.title = "删除失败:" + err.message;
  }
}

async function load() {
  const list = $("note-list");
  list.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await fetch("/api/wiki", { headers: authHeaders() });
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    if (!data.enabled) {
      list.innerHTML = '<div class="empty">知识库未启用(设置 MEDIASCRIBE_WIKI=1)</div>';
      return;
    }
    $("vault-path").textContent = data.path || "";
    list.innerHTML = "";
    const notes = data.notes || [];
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
load();
