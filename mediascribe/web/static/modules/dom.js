// 共享 DOM 引用与工具 —— 其它模块从此处取元素句柄。
// 放在最前：ES module 按依赖图求值，dom 先于使用方执行。
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------
// 主题: 月之暗面 (dark) / 月之亮面 (light) — head 内脚本已设初始值
// ---------------------------------------------------------------------

const form = $("form");

const statusEl = $("status");

const results = $("results");

const submit = $("submit");

const emptyState = $("empty-state");

const activeJobs = new Set();

const gpuPill = $("gpu-pill");

const gpuLabel = $("gpu-label");

const tokenInput = $("api-token");

const rememberToken = $("remember-token");

// 鉴权状态徽标 —— 服务端 /api/health 的 auth_enabled 决定文案。
const authBadge = $("auth-badge");

try { tokenInput.value = localStorage.getItem("v2t_api_token") || ""; } catch (_) { /* storage 不可用 */ }

//: 本机保存的 job_id 列表 —— 刷新页面后据此恢复队列卡片。
//: 键名带 v2t_ 前缀, 与 api token 的存储键保持同一套前缀惯例。
const JOB_STORE_KEY = "v2t_job_ids";

function loadStoredJobIds() {
  try {
    const raw = localStorage.getItem(JOB_STORE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : [];
  } catch (_) {
    return [];
  }
}

function storeJobIds(ids) {
  try {
    localStorage.setItem(JOB_STORE_KEY, JSON.stringify(ids.slice(-50)));
  } catch (_) { /* storage 不可用 */ }
}

function rememberJobId(jobId) {
  const ids = loadStoredJobIds();
  if (!ids.includes(jobId)) {
    ids.push(jobId);
    storeJobIds(ids);
  }
}

function forgetJobId(jobId) {
  storeJobIds(loadStoredJobIds().filter((x) => x !== jobId));
}

tokenInput.addEventListener("change", () => {
  try {
    if (tokenInput.value && rememberToken.checked) localStorage.setItem("v2t_api_token", tokenInput.value);
    else localStorage.removeItem("v2t_api_token");
  } catch (_) { /* storage 不可用 */ }
});

rememberToken.addEventListener("change", () => {
  try {
    if (!rememberToken.checked) localStorage.removeItem("v2t_api_token");
    else if (tokenInput.value) localStorage.setItem("v2t_api_token", tokenInput.value);
  } catch (_) { /* storage 不可用 */ }
});

export {
  $,
  activeJobs,
  authBadge,
  emptyState,
  forgetJobId,
  form,
  gpuLabel,
  gpuPill,
  loadStoredJobIds,
  rememberJobId,
  rememberToken,
  results,
  statusEl,
  storeJobIds,
  submit,
  tokenInput,
};
