// 状态条、机台指示与 HTML 转义。
import { activeJobs, statusEl } from './dom.js';

function setStatus(msg, isError) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("err", Boolean(isError));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------------------------------------------------------------------
// 机台指示: 任务运行计数 — 有活跃任务时出现
// ---------------------------------------------------------------------

function updateMeters() {
  const n = activeJobs.size;
  $("rec-light").classList.toggle("on", n > 0);
  $("rec-count").textContent = n > 0 ? `${n} 个任务运行中` : "";
}

// ---------------------------------------------------------------------
// GPU 状态: 轮询 /api/health, 每 5 秒刷新
// ---------------------------------------------------------------------

export { escapeHtml, setStatus, updateMeters };
