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

try { tokenInput.value = localStorage.getItem("v2t_api_token") || ""; } catch (_) { /* storage 不可用 */ }

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

export { $, activeJobs, emptyState, form, gpuLabel, gpuPill, rememberToken, results, statusEl, submit, tokenInput };
