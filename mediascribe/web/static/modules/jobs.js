// 任务流：提交转录、渲染任务卡、WebSocket 进度、结果取回。
//
// 2026-10-31 交互审查后重写, 覆盖四条主流程缺陷:
//   P0-1  status.js 缺 `$` 导入 ⇒ updateMeters 抛错 ⇒ 任务卡渲染中断,
//         WebSocket 从未建立, 用户永远拿不到文稿。
//   P0-2  长转写(>300s)期间服务端无事件 ⇒ WS 被空闲超时掐断。
//         ⇒ 客户端加心跳(pong) + 断线自动重连, 重连后按快照补状态。
//   P0-3  刷新页面后任务全丢(后端无列举接口, 前端不存 job_id)。
//         ⇒ 后端补 GET /api/jobs, 前端持久化 job_id 并在加载时恢复。
//   P1-4  取结果失败静默 return ⇒ 卡片显示「完成」却没有任何文稿。
//   P1-5  422 的 detail 是数组 ⇒ 错误提示变成一坨 dict repr。
import {
  $,
  activeJobs,
  emptyState,
  forgetJobId,
  form,
  loadStoredJobIds,
  rememberJobId,
  results,
  submit,
} from './dom.js';
import { escapeHtml, readError, setStatus, updateMeters } from './status.js';
import { authHeaders, wsProtocols, wsUrl } from './auth.js';
import { ICONS } from './theme.js';

const STAGES = ["download", "extract_audio", "transcribe", "merge"];

const STAGE_LABELS = {
  download: "下载",
  extract_audio: "提取音频",
  transcribe: "转写",
  merge: "合并",
};

//: 重连退避序列(毫秒) —— 指数级, 上限 15s。
const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 15000];

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
  if (urls.length > 20) {
    // 与后端 max_length=20 保持一致 —— 提前拦下, 不让用户等一个 422。
    setStatus(`一次最多提交 20 条链接(当前 ${urls.length} 条),请分批提交。`, true);
    return;
  }
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
      // P1-5: 422 的 detail 是数组 —— 统一走 readError 转成可读文案。
      throw new Error(await readError(resp));
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

function renderJobCard(job, opts = {}) {
  // 2026-10-31 独立盲审 P1-4: 提交路径与 restoreQueue 可能对**同一个
  // job_id** 各渲染一次(GET /api/jobs 的响应先到、POST /api/jobs 的响应
  // 后到) ⇒ 同一任务出现两张卡、两条 WS、两份文稿。
  // 统一在这里按 job_id 去重 —— 谁先渲染谁占位, 后来的直接复用。
  const existing = cardExists(job.job_id);
  if (existing) return existing;

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
  rememberJobId(job.job_id);
  updateMeters();
  // 取消按钮的监听器**只在这里注册一次**。
  // 2026-10-31 自查发现: 若把它放进 connectWs, 每次断线重连都会对同一个
  // 按钮再 addEventListener ⇒ 重连 N 次后点一次「终止」会发出 N 个
  // cancel 请求。终止是**破坏性**操作, 必须保证一次点击 = 一次请求。
  const cancelBtn = card.querySelector("[data-cancel]");
  cancelBtn.addEventListener("click", async () => {
    cancelBtn.disabled = true;
    try {
      const r = await fetch(`/api/jobs/${job.job_id}/cancel`, { method: "POST", headers: authHeaders() });
      setStatus(r.ok ? `已请求取消 ${job.job_id.slice(0, 8)}` : "取消失败:任务不存在或已结束", !r.ok);
      if (!r.ok) cancelBtn.disabled = false;
    } catch (_) {
      setStatus("取消失败:无法连接服务器", true);
      cancelBtn.disabled = false;
    }
  });
  // 终态任务**不建 WS**。
  // 2026-10-31 独立盲审 P0-1: 服务端对已结束的 job 会发**两次** snapshot
  // (route_ws.py 的连接时快照 + 收尾快照) ⇒ 客户端会两次触发 fetchResult
  // ⇒ 加上 restoreQueue 自己那一次, 同一篇文稿被渲染 3 份、下载按钮绑 3 个
  // 监听器 ⇒ 点一次下载触发 3 次下载。
  // 终态任务没有"进度"可订阅, 直接走结果渲染即可。
  if (opts.skipWs) {
    cancelBtn.style.display = "none";
    activeJobs.delete(job.job_id);
    updateMeters();
  } else {
    connectWs(card, job);
  }
  return card;
}

function connectWs(card, job) {
  const nodes = {};
  const segs = {};
  card.querySelectorAll(".node").forEach((n) => { nodes[n.dataset.node] = n; });
  STAGES.forEach((s, i) => { if (i > 0) segs[s] = card.querySelectorAll(".seg")[i - 1]; });
  const chip = card.querySelector("[data-chip]");
  const stageLabel = card.querySelector("[data-stage-label]");
  const pctEl = card.querySelector("[data-pct]");
  const errEl = card.querySelector("[data-err]");
  const cancelBtn = card.querySelector("[data-cancel]");

  //: 本卡片的重连状态 —— 挂在 card 上, 跨 connectWs 调用存活。
  //: (旧实现把这些放在 connectWs 闭包里 ⇒ 每次重连都重置退避计数,
  //:  且旧 socket 的 onclose 会再排一次重连 ⇒ 重连风暴 + WS 泄漏。)
  const state = card.__wsState || (card.__wsState = { attempt: 0, timer: null, socket: null, stopped: false });

  //: 当前 socket 与心跳定时器(每次 connectWs 一份)。
  let ws = null;
  let heartbeat = null;

  function setChip(chipState, text) {
    chip.className = "chip " + chipState;
    chip.textContent = text;
  }

  // 运行态微光: 阶段名与百分比流动, 直至进入终态
  function setRunning(on) {
    stageLabel.classList.toggle("running-text", on);
    pctEl.classList.toggle("running-text", on);
  }

  function finishCard(finalState, chipText) {
    card.classList.remove("ok", "err", "cancelled");
    card.classList.add(finalState);
    setChip(finalState === "cancelled" ? "cancelled" : finalState, chipText);
    activeJobs.delete(job.job_id);
    updateMeters();
    // 终态 ⇒ 彻底停止重连 —— 否则任务已经结束还会反复重连。
    state.stopped = true;
    if (state.timer) { clearTimeout(state.timer); state.timer = null; }
    // 终态后终止按钮不再有意义 —— 留着只会让用户点出「取消失败」。
    cancelBtn.style.display = "none";
    // 有新的已结束卡片 ⇒ 「清空已结束」按钮该可用了。
    refreshClearButton();
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
    forgetJobId(job.job_id);
  }

  // ------------------------------------------------------------------
  // 心跳: 定期回 pong, 让服务端的空闲计时器知道连接还活着。
  // P0-2 —— 转写阶段可以几分钟不发任何事件, 没有心跳就会被掐断。
  // ------------------------------------------------------------------
  function startHeartbeat() {
    stopHeartbeat();
    heartbeat = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ event: "pong" })); } catch (_) { /* 连接已断, 交给 onclose */ }
      }
    }, 20000);
  }

  function stopHeartbeat() {
    if (heartbeat) { clearInterval(heartbeat); heartbeat = null; }
  }

  // 重连退避 —— 指数级, 上限 15s。
  // 状态在 card 上(见函数顶部), 跨重连存活。
  function scheduleReconnect() {
    if (state.stopped) return;
    if (!activeJobs.has(job.job_id)) return;
    if (state.timer) return; // 已排过一次, 不重复排
    const delay = RECONNECT_DELAYS[Math.min(state.attempt, RECONNECT_DELAYS.length - 1)];
    state.attempt += 1;
    setChip("busy", "重连中");
    state.timer = setTimeout(() => {
      state.timer = null;
      if (state.stopped || !activeJobs.has(job.job_id)) return;
      connectWs(card, job);
    }, delay);
  }

  // 连接彻底建立并收到第一条消息 ⇒ 退避计数归零。
  function markConnected() {
    state.attempt = 0;
  }

  // 取消按钮的监听器在 renderJobCard 里注册(只此一次) —— 这里**不能**再注册,
  // 否则每次重连都会叠加一个监听器 ⇒ 点一次「终止」发出 N 个 cancel 请求。

  function open() {
    ws = new WebSocket(wsUrl(`/ws/progress/${job.job_id}`), wsProtocols());
    // 记住"当前 socket" —— 旧 socket 的 onclose 必须能被识别为过期,
    // 否则旧连接的关闭会再排一次重连(重连风暴)。
    state.socket = ws;
    const thisSocket = ws;

    ws.onopen = () => {
      startHeartbeat();
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }
      if (!msg || typeof msg !== "object") return;
      const stage = msg.stage;
      const evt = msg.event;
      if (evt === "error") {
        // 2026-10-31 独立盲审 P1-3: 服务端对未知 job 发 error 事件并以
        // 4404 关闭(见 route_ws.py)。旧实现忽略 error 事件 ⇒ onclose 排
        // 重连 ⇒ 但每轮都收到这条 error 消息 ⇒ 退避计数被清零 ⇒ **每秒
        // 无限重连**, 卡片永远停在「重连中」且无任何提示。
        //
        // 现在: error 事件 ⇒ 终止重连 + 在卡片上给出可读原因。
        state.stopped = true;
        if (state.timer) { clearTimeout(state.timer); state.timer = null; }
        stopHeartbeat();
        errEl.textContent = msg.message
          ? `进度连接被服务端拒绝:${msg.message}`
          : "进度连接被服务端拒绝 — 该任务可能已过期或服务已重启。";
        errEl.classList.add("show");
        stageLabel.textContent = "已断开";
        stageLabel.dataset.state = "err";
        setRunning(false);
        setChip("err", "已断开");
        activeJobs.delete(job.job_id);
        updateMeters();
        cancelBtn.style.display = "none";
        return;
      }
      if (evt === "ping") {
        // 回 pong 保活 —— 服务端据此刷新空闲计时器。
        if (ws && ws.readyState === WebSocket.OPEN) {
          try { ws.send(JSON.stringify({ event: "pong" })); } catch (_) { /* 忽略 */ }
        }
        // ping 携带 job 快照 ⇒ 顺带把阶段/进度补齐(重连后事件不回放)。
        if (msg.stage && !msg.finished && !msg.cancelled) {
          setStage(msg.stage, pctOf(msg), false);
        }
        return;
      }
      if (evt === "snapshot") {
        if (msg.cancelled) { markCancelled(); return; }
        if (msg.finished) {
          // 重连时任务已经跑完 —— 走终态分支, 别让卡片永远转圈。
          applyFinished(msg);
          return;
        }
        if (msg.stage) setStage(msg.stage, pctOf(msg), false);
        return;
      }
      if (evt === "stage_start") {
        // 收到**进度**事件 ⇒ 连接真的可用 ⇒ 退避计数归零。
        // (只在进度事件上归零 —— ping/error 消息不代表进度。)
        markConnected();
        setChip("busy", "运行中"); setRunning(true); setStage(stage, 0, false);
      }
      else if (evt === "stage_progress") {
        markConnected();
        setChip("busy", "运行中"); setRunning(true); setStage(stage, msg.total > 0 ? (msg.current / msg.total) * 100 : 0, false);
      }
      else if (evt === "stage_done") { setStage(stage, 100, true); }
      else if (evt === "succeeded") {
        setStage("merge", 100, true);
        setRunning(false);
        finishCard("ok", "完成");
        stageLabel.textContent = "完成";
        stageLabel.dataset.state = "ok";
        pctEl.textContent = "100%";
        // 心跳在这里就停 —— 三个终态分支对称清理。
        // 旧实现只依赖服务端随后关闭连接触发 onclose 来停心跳; 若那次关闭
        // 因网络分区没到达, 心跳的 setInterval 会一直留着(每个 job 一个,
        // 每 20s 发一次 pong)。终态是**确定**的, 不该依赖对端善后。
        stopHeartbeat();
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
        stopHeartbeat();
        ws.close();
      } else if (evt === "cancelled" || evt === "cancelled_done") {
        markCancelled();
        stopHeartbeat();
        ws.close();
      }
    };

    ws.onerror = () => { /* 以服务端 close 为准 */ };
    ws.onclose = (ev) => {
      // 旧 socket 的关闭**不能**再触发重连 —— 否则每条过期连接都会
      // 多排一次重连 ⇒ 重连风暴 + WS 泄漏。只有当前 socket 才有资格。
      if (state.socket !== thisSocket) return;
      stopHeartbeat();
      // 2026-10-31 独立盲审 P1-3: 服务端用特定 close code 表示
      // "别再连了" —— 4404(unknown job) / 1008(鉴权失败) / 1011(内部错误)。
      // 这些情况重连多少次都没用, 只会每秒打一次服务端。
      const code = ev && typeof ev.code === "number" ? ev.code : 0;
      if (code === 4404 || code === 1008 || code === 1011) {
        state.stopped = true;
        if (state.timer) { clearTimeout(state.timer); state.timer = null; }
        setRunning(false);
        setChip("err", "已断开");
        errEl.textContent =
          code === 4404
            ? "任务已不存在(可能服务已重启或任务已过期) — 请重新提交。"
            : code === 1008
              ? "进度连接鉴权失败 — 请检查 API 令牌后重新提交。"
              : "进度连接因服务端错误而断开 — 请稍后重新提交。";
        errEl.classList.add("show");
        activeJobs.delete(job.job_id);
        updateMeters();
        cancelBtn.style.display = "none";
        return;
      }
      // 终态事件会先把 job 移出 activeJobs 再主动 close; 走到这里说明
      // 连接是意外断开的(如空闲超时) — 尝试重连, 不再让指示灯空转。
      if (activeJobs.has(job.job_id) && !state.stopped) {
        setRunning(false);
        scheduleReconnect();
      }
    };
  }

  // 快照里的进度换算成百分比。
  function pctOf(msg) {
    return msg.stage_total > 0 ? (msg.stage_current / msg.stage_total) * 100 : 0;
  }

  // 重连后发现任务已终态 —— 按 succeeded / failed 补齐卡片。
  function applyFinished(msg) {
    stopHeartbeat();
    if (msg.cancelled) { markCancelled(); return; }
    if (msg.error) {
      errEl.textContent = msg.error;
      errEl.classList.add("show");
      stageLabel.textContent = "失败";
      stageLabel.dataset.state = "err";
      setRunning(false);
      finishCard("err", "失败");
      return;
    }
    setStage("merge", 100, true);
    setRunning(false);
    finishCard("ok", "完成");
    stageLabel.textContent = "完成";
    stageLabel.dataset.state = "ok";
    pctEl.textContent = "100%";
    fetchResult(card, job);
  }

  open();
}

async function fetchResult(card, job) {
  const errEl = card.querySelector("[data-err]");
  const copyBtn = card.querySelector("[data-copy]");
  const dlBtn = card.querySelector("[data-download]");

  // 幂等: 同一张卡片可能被多次要求取结果(restoreQueue 自己一次 +
  // 服务端 snapshot 一次 + WS 终态一次) ⇒ 必须防止重复渲染文稿、
  // 重复绑定复制/下载监听器(否则点一次下载会触发 N 次下载)。
  if (card.__resultRendered) return;
  card.__resultRendered = true;

  // 取结果的退避重试。
  //
  // 2026-10-31 独立盲审 P0-2: 服务端的 ``job.succeed()``(progress.py:254)
  // 比 ``_store_job_result`` 写入 job_results(job_runner.py:75)**更早** ——
  // 中间还夹着读输出文件 + wiki 归档。客户端收到 succeeded 事件就立刻拉
  // /result, 此时结果往往还没落盘 ⇒ markdown=None。
  //
  // 旧文案把它断言成"文稿内容为空(可能视频没有语音内容)" —— 那是**错误
  // 结论**(视频有语音, 文稿确实存在), 而且不会自愈。现在改为退避重试,
  // 重试耗尽才提示"结果尚未就绪"。
  const RETRY_DELAYS = [0, 400, 1200, 2500];

  for (let attempt = 0; attempt < RETRY_DELAYS.length; attempt += 1) {
    if (RETRY_DELAYS[attempt] > 0) {
      await new Promise((r) => setTimeout(r, RETRY_DELAYS[attempt]));
    }
    try {
      const r = await fetch(`/api/jobs/${job.job_id}/result`, { headers: authHeaders() });
      // P1-4: 取不到结果**必须报错** —— 旧代码 `if (!r.ok) return;` 会让卡片
      // 显示「完成」却没有文稿、没有按钮、没有任何提示, 用户以为转写成功了。
      if (!r.ok) {
        const msg = await readError(r);
        errEl.textContent = `转写已完成, 但取回文稿失败:${msg}`;
        errEl.classList.add("show");
        return;
      }
      const data = await r.json();
      if (!data.markdown) {
        // 结果尚未落盘 —— 继续重试, 不下"内容为空"的结论。
        if (attempt < RETRY_DELAYS.length - 1) continue;
        errEl.textContent = "转写已完成, 但结果尚未就绪 — 稍后刷新页面可在队列中重新取回。";
        errEl.classList.add("show");
        return;
      }
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
      return;
    } catch (err) {
      // 网络异常 —— 若是最后一次尝试才报错。
      if (attempt < RETRY_DELAYS.length - 1) continue;
      // P1-4 同理: 网络异常也不能静默。
      errEl.textContent = `转写已完成, 但取回文稿失败:${err.message}`;
      errEl.classList.add("show");
      return;
    }
  }
}

// ---------------------------------------------------------------------
// P0-3: 刷新页面后恢复队列
// ---------------------------------------------------------------------

async function restoreQueue() {
  // 2026-10-31 交互审查 P0-3 的实现要点:
  // 恢复**不能**以本地存储为前置条件 —— 本地存储只是"我记得哪些 job"的
  // 辅助, 服务端的 GET /api/jobs 才是事实源。若把 ids.length === 0 当作
  // 早退条件, 就会复现原缺陷的另一半: 换了浏览器/清了缓存后, 服务端明明
  // 还在跑任务, 页面却一片空白。所以这里无条件向服务端问一次。
  let data;
  try {
    const r = await fetch("/api/jobs", { headers: authHeaders() });
    if (!r.ok) return;
    data = await r.json();
  } catch (_) {
    return;
  }
  const jobs = Array.isArray(data.jobs) ? data.jobs : [];
  if (jobs.length === 0) {
    // 服务端已无任务 —— 顺手清掉本地存储里的过期 job_id。
    loadStoredJobIds().forEach(forgetJobId);
    return;
  }
  const known = new Set(jobs.map((j) => j.job_id));
  // 服务端已不认识的 job(过期/已清理) —— 从本地存储里清掉, 不留垃圾。
  loadStoredJobIds().filter((id) => !known.has(id)).forEach(forgetJobId);
  emptyState.style.display = "none";
  for (const job of jobs) {
    // 终态任务走 skipWs 路径 —— 不建 WS(服务端会对已结束任务发两次
    // snapshot, 导致文稿被渲染多份)。renderJobCard 内部已按 job_id 去重。
    const card = renderJobCard(job, { skipWs: Boolean(job.finished) });
    if (!card) continue;
    if (job.finished) {
      // 任务在刷新前就跑完了 —— 直接补结果, 不必再连 WS。
      //
      // 终态要**分三种**: 完成 / 失败 / 已取消。旧实现(以及第一版修复)
      // 一律标成"完成" ⇒ 失败的任务刷新后显示绿色"完成", 已取消的也显示
      // "完成" —— 用户完全被误导。
      const chip = card.querySelector("[data-chip]");
      const stageLabel = card.querySelector("[data-stage-label]");
      const pct = card.querySelector("[data-pct]");

      if (job.cancelled) {
        card.classList.add("cancelled");
        chip.className = "chip cancelled";
        chip.textContent = "已取消";
        stageLabel.textContent = "已取消";
        stageLabel.dataset.state = "off";
        pct.textContent = "—";
        forgetJobId(job.job_id);
        continue;
      }
      if (job.error) {
        const errEl = card.querySelector("[data-err]");
        errEl.textContent = job.error;
        errEl.classList.add("show");
        card.classList.add("err");
        chip.className = "chip err";
        chip.textContent = "失败";
        stageLabel.textContent = "失败";
        stageLabel.dataset.state = "err";
        pct.textContent = "—";
        continue;
      }
      card.classList.add("ok");
      chip.className = "chip ok";
      chip.textContent = "完成";
      stageLabel.textContent = "完成";
      stageLabel.dataset.state = "ok";
      pct.textContent = "100%";
      fetchResult(card, job);
    }
  }
  // 恢复出来的终态卡片也算「已结束」⇒ 刷新清空按钮的可用状态。
  refreshClearButton();
}

// 查找已存在的任务卡片 —— 返回**元素本身**(没有则 null)。
//
// 注意: 早前这里返回 Boolean(...) ⇒ renderJobCard 的
// "if (existing) return existing" 会把 true 当成卡片返回, 调用方一
// querySelector 就崩。返回元素才能让"复用已有卡片"真正可用。
function cardExists(jobId) {
  return results.querySelector(`.card[data-job-id="${jobId}"]`) || null;
}

// ---------------------------------------------------------------------
// 清空已结束(ok / 失败 / 已取消)的任务卡片
// ---------------------------------------------------------------------

// 2026-10-31 交互审查(按钮与状态反馈): 「清空已结束」原本永远可点, 但
// 没有可清的东西时点了**毫无反应** —— 用户不知道是按钮坏了还是没东西可清。
// 现在按实际可清数量启用/禁用。
function finishedCards() {
  return [...results.querySelectorAll(".card")].filter(
    (c) => c.classList.contains("ok") || c.classList.contains("err") || c.classList.contains("cancelled")
  );
}

function refreshClearButton() {
  const btn = $("clear-done");
  if (!btn) return;
  const n = finishedCards().length;
  btn.disabled = n === 0;
  btn.textContent = n > 0 ? `清空已结束 (${n})` : "清空已结束";
  btn.title = n > 0 ? `移除 ${n} 个已结束的任务卡片` : "没有已结束的任务";
}

$("clear-done").addEventListener("click", () => {
  const done = finishedCards();
  done.forEach((c) => {
    const id = c.dataset.jobId;
    if (id) forgetJobId(id);
    c.remove();
  });
  if (!results.querySelector(".card")) emptyState.style.display = "";
  refreshClearButton();
});

refreshClearButton();

restoreQueue();

export { RECONNECT_DELAYS, STAGES, STAGE_LABELS, connectWs, fetchResult, renderJobCard, restoreQueue };
