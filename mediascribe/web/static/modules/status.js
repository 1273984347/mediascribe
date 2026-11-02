// 状态条、机台指示与 HTML 转义。
import { $, activeJobs, statusEl } from './dom.js';

function setStatus(msg, isError) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("err", Boolean(isError));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// 把后端错误响应转成**人能读的一句话**。
//
// 2026-10-31 交互审查 P1-5: FastAPI 的 422 校验错误里 `detail` 是**数组**
// (形如 [{type, loc, msg, input, ctx}]) —— 旧代码 `new Error(err.detail)` 会把
// 整个数组 toString 成一坨 Python dict repr, 连同全部 21 条 URL 一起喷到状态栏。
// 这里统一按形状分派: 数组 → 取每项的 msg; 字符串 → 原样; 其它 → JSON 兜底。
function detailToText(detail) {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (typeof d === "string") return d;
        if (d && typeof d === "object") {
          const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
          const msg = d.msg || d.type || JSON.stringify(d);
          return loc ? `${loc}: ${msg}` : msg;
        }
        return String(d);
      })
      .filter(Boolean);
    return parts.join("; ");
  }
  if (typeof detail === "object") {
    const msg = detail.msg || detail.message || detail.error;
    if (msg) return String(msg);
    try {
      return JSON.stringify(detail);
    } catch (_) {
      return String(detail);
    }
  }
  return String(detail);
}

// 从一个 Response 里提取可读错误 —— 处理非 JSON 响应体(网关/反代返回 HTML)。
async function readError(resp) {
  let detail = null;
  try {
    const data = await resp.json();
    detail = data && typeof data === "object" ? data.detail ?? data : data;
  } catch (_) {
    detail = null;
  }
  const text = detailToText(detail);
  if (text) return text;
  if (resp.status === 401 || resp.status === 403) {
    return resp.status === 401 ? "未授权 — 请在下方填入正确的 API 令牌" : "令牌无效或来源被拒绝";
  }
  if (resp.status === 429) return "请求过于频繁 — 请稍后再试";
  return `请求失败 (HTTP ${resp.status})`;
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

export { detailToText, escapeHtml, readError, setStatus, updateMeters };
