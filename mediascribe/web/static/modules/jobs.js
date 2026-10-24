// 任务流：提交转录、渲染任务卡、WebSocket 进度、结果取回。
import { $, activeJobs, emptyState, form, results, submit } from './dom.js';
import { escapeHtml, setStatus, updateMeters } from './status.js';
import { authHeaders, wsProtocols, wsUrl } from './auth.js';
import { ICONS } from './theme.js';

const STAGES = ["download", "extract_audio", "transcribe", "merge"];

const STAGE_LABELS = {
  download: "下载",
  extract_audio: "提取音频",
  transcribe: "转写",
  merge: "合并",
};

$("urls").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    form.requestSubmit();
  }
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const urls = $("urls").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  if (urls.length === 0) { setStatus("请先粘贴至少一条链接。", true); return; }
  submit.disabled = true;
  setStatus(`正在提交 ${urls.length} 条链接…`);
  try {
    const body = {
      urls,
      engine: $("engine").value,
      model: $("model").value,
      language: $("language").value || null,
      ocr_engine: $("ocr").value,
      wechat_cookies: $("cookies").value || null,
      bilingual: $("bilingual").checked,
    };
    const resp = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const data = await resp.json();
    const jobs = data.jobs || [];
    let msg = `已提交 ${jobs.length} 个任务,信号链启动…`;
    if (data.requested > data.unique) msg += `(已合并 ${data.requested - data.unique} 条重复链接)`;
    // 资源守卫的降档说明 —— 服务端在构建 pipeline 时就已决定降档(见
    // mediascribe/runtime_guard.py),提交响应里直接带回,用户**当场**知道
    // 「这次为什么用了小模型」,而不是转写完才在 metadata 里看到。
    const notices = Array.isArray(data.notices) ? data.notices : [];
    setStatus(notices.length ? `${msg} · ${notices.join("; ")}` : msg, notices.length > 0);
    emptyState.style.display = "none";
    for (const job of jobs) renderJobCard(job);
  } catch (err) {
    setStatus("提交失败:" + err.message, true);
  } finally {
    submit.disabled = false;
  }
});

// ---------------------------------------------------------------------
// 任务卡片 + WS 进度 + 取消 + 结果拉取
// ---------------------------------------------------------------------

function renderJobCard(job) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.jobId = job.job_id;
  card.innerHTML = `
    <div class="card-top">
      <span class="chip" data-chip>排队中</span>
      <span class="url" title="${escapeHtml(job.url)}">${escapeHtml(job.url)}</span>
      <span class="jid">#${escapeHtml(job.job_id.slice(0, 8))}</span>
    </div>
    <div class="chain" aria-label="处理进度">
      ${STAGES.map((s, i) => `
        ${i > 0 ? '<span class="seg"><i></i></span>' : ""}
        <span class="node" data-node="${s}" title="${STAGE_LABELS[s]}">${String(i + 1).padStart(2, "0")}</span>
      `).join("")}
    </div>
    <p class="chain-meta"><span class="cm-label">阶段</span><span data-stage-label>—</span><span data-pct>—</span></p>
    <p class="errmsg" data-err></p>
    <div class="toolbar">
      <button type="button" data-cancel class="danger">终止</button>
      <button type="button" data-copy style="display:none;">复制文稿</button>
      <button type="button" data-download style="display:none;">下载 .md</button>
    </div>`;
  results.appendChild(card);
  activeJobs.add(job.job_id);
  updateMeters();
  connectWs(card, job);
}

function connectWs(card, job) {
  const ws = new WebSocket(wsUrl(`/ws/progress/${job.job_id}`), wsProtocols());
  const nodes = {};
  const segs = {};
  card.querySelectorAll(".node").forEach((n) => { nodes[n.dataset.node] = n; });
  STAGES.forEach((s, i) => { if (i > 0) segs[s] = card.querySelectorAll(".seg")[i - 1]; });
  const chip = card.querySelector("[data-chip]");
  const stageLabel = card.querySelector("[data-stage-label]");
  const pctEl = card.querySelector("[data-pct]");
  const errEl = card.querySelector("[data-err]");

  function setChip(state, text) {
    chip.className = "chip " + state;
    chip.textContent = text;
  }

  // 运行态微光: 阶段名与百分比流动, 直至进入终态
  function setRunning(on) {
    stageLabel.classList.toggle("running-text", on);
    pctEl.classList.toggle("running-text", on);
  }

  function finishCard(state, chipText) {
    card.classList.remove("ok", "err", "cancelled");
    card.classList.add(state);
    setChip(state === "cancelled" ? "cancelled" : state, chipText);
    activeJobs.delete(job.job_id);
    updateMeters();
  }

  function resetNode(s) {
    const n = nodes[s];
    n.className = "node";
    n.textContent = String(STAGES.indexOf(s) + 1).padStart(2, "0");
  }

  function setStage(stage, pct, done) {
    const idx = STAGES.indexOf(stage);
    if (idx < 0) return;
    STAGES.forEach((s, i) => {
      if (i < idx || (i === idx && done)) { nodes[s].className = "node done"; nodes[s].textContent = "✓"; }
      else if (i === idx) { nodes[s].className = "node active"; nodes[s].innerHTML = ICONS.play; }
      else resetNode(s);
    });
    STAGES.forEach((s, j) => {
      if (j === 0) return;
      // segs[s] 是"流入节点 j"的那段导线 — 代表阶段 j-1 的进度
      const seg = segs[s];
      const prev = j - 1;
      const fill = seg.querySelector("i");
      if (prev < idx || (prev === idx && done)) { seg.className = "seg done"; fill.className = ""; fill.style.width = "100%"; }
      else if (prev === idx) {
        seg.className = "seg";
        fill.className = "shimmer-bar";
        fill.style.width = pct + "%";
      }
      else { seg.className = "seg"; fill.className = ""; fill.style.width = "0%"; }
    });
    stageLabel.textContent = STAGE_LABELS[stage] || stage;
    delete stageLabel.dataset.state;
    pctEl.textContent = Math.round(pct) + "%";
  }

  function markCancelled() {
    STAGES.forEach((s) => { resetNode(s); nodes[s].classList.add("off"); });
    STAGES.forEach((s, i) => {
      if (i > 0) { const f = segs[s].querySelector("i"); segs[s].className = "seg"; f.className = ""; f.style.width = "0%"; }
    });
    stageLabel.textContent = "已取消";
    stageLabel.dataset.state = "off";
    pctEl.textContent = "—";
    setRunning(false);
    finishCard("cancelled", "已取消");
  }

  card.querySelector("[data-cancel]").addEventListener("click", async () => {
    try {
      const r = await fetch(`/api/jobs/${job.job_id}/cancel`, { method: "POST", headers: authHeaders() });
      setStatus(r.ok ? `已请求取消 ${job.job_id.slice(0, 8)}` : "取消失败:任务不存在或已结束", !r.ok);
    } catch (_) { /* 网络异常, WS 终态为准 */ }
  });

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (!msg || typeof msg !== "object") return;
    const stage = msg.stage;
    const evt = msg.event;
    if (evt === "ping" || evt === "snapshot") {
      if (evt === "snapshot" && msg.cancelled) markCancelled();
      return;
    }
    if (evt === "stage_start") { setChip("busy", "运行中"); setRunning(true); setStage(stage, 0, false); }
    else if (evt === "stage_progress") { setChip("busy", "运行中"); setRunning(true); setStage(stage, msg.total > 0 ? (msg.current / msg.total) * 100 : 0, false); }
    else if (evt === "stage_done") { setStage(stage, 100, true); }
    else if (evt === "succeeded") {
      setStage("merge", 100, true);
      setRunning(false);
      finishCard("ok", "完成");
      stageLabel.textContent = "完成";
      stageLabel.dataset.state = "ok";
      pctEl.textContent = "100%";
      fetchResult(card, job);
    } else if (evt === "failed") {
      const idx = STAGES.indexOf(stage);
      STAGES.forEach((s, i) => { if (i < idx) { nodes[s].className = "node done"; nodes[s].textContent = "✓"; } });
      const bad = nodes[stage] || nodes[STAGES[STAGES.length - 1]];
      if (bad) { bad.className = "node fail"; bad.textContent = "×"; }
      errEl.textContent = msg.error || "未知错误";
      errEl.classList.add("show");
      stageLabel.textContent = "失败";
      stageLabel.dataset.state = "err";
      setRunning(false);
      finishCard("err", "失败");
      ws.close();
    } else if (evt === "cancelled" || evt === "cancelled_done") {
      markCancelled();
      ws.close();
    }
  };
  ws.onerror = () => { /* 以服务端 close 为准 */ };
  ws.onclose = () => {
    // 终态事件会先把 job 移出 activeJobs 再主动 close; 走到这里说明
    // 连接是意外断开的(如空闲超时) — 提示用户, 不再让指示灯空转。
    if (activeJobs.has(job.job_id)) {
      activeJobs.delete(job.job_id);
      updateMeters();
      setRunning(false);
      chip.className = "chip cancelled";
      chip.textContent = "连接断开";
      setStatus("进度连接已断开(任务可能仍在后台运行),可稍后刷新页面查看结果。", true);
    }
  };
}

async function fetchResult(card, job) {
  try {
    const r = await fetch(`/api/jobs/${job.job_id}/result`, { headers: authHeaders() });
    if (!r.ok) return;
    const data = await r.json();
    if (!data.markdown) return;
    const dateStr = new Date().toLocaleDateString("zh-CN");
    const archived = data.wiki && data.wiki.raw
      ? `<span class="result-archived" title="已归档到知识库: ${escapeHtml(data.wiki.raw)}">已归档知识库</span>`
      : "";
    const wrap = document.createElement("div");
    wrap.className = "result";
    wrap.innerHTML = `
      <div class="result-head">
        <span>转写文稿</span>
        <span class="result-title">${escapeHtml(data.title || "未命名文稿")}</span>
        ${archived}
        <span>${escapeHtml(dateStr)}</span>
      </div>
      <div class="result-body"></div>`;
    wrap.querySelector(".result-body").textContent = data.markdown;
    card.appendChild(wrap);
    const copyBtn = card.querySelector("[data-copy]");
    const dlBtn = card.querySelector("[data-download]");
    copyBtn.style.display = "";
    dlBtn.style.display = "";
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(data.markdown);
        setStatus(`已复制文稿:${data.title || job.url}`);
        const label = copyBtn.textContent;
        copyBtn.textContent = "✓ 已复制";
        setTimeout(() => { copyBtn.textContent = label; }, 1500);
      } catch (_) { setStatus("复制失败:浏览器拒绝了剪贴板访问", true); }
    });
    dlBtn.addEventListener("click", () => {
      const blob = new Blob([data.markdown], { type: "text/markdown" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (data.title || job.url).replace(/[^a-z0-9\u4e00-\u9fff]+/gi, "_").slice(0, 60) + ".md";
      a.click();
      URL.revokeObjectURL(a.href);
    });
  } catch (_) { /* 网络异常 */ }
}

// 清空已结束(ok / 失败 / 已取消)的任务卡片

$("clear-done").addEventListener("click", () => {
  const done = [...results.querySelectorAll(".card")].filter(
    (c) => c.classList.contains("ok") || c.classList.contains("err") || c.classList.contains("cancelled")
  );
  done.forEach((c) => c.remove());
  if (!results.querySelector(".card")) emptyState.style.display = "";
});
  

export { STAGES, STAGE_LABELS, connectWs, fetchResult, renderJobCard };
