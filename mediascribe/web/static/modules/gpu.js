// GPU 状态指示：轮询 /api/health，每 5 秒刷新。
import { authBadge, gpuLabel, gpuPill } from './dom.js';

// 2026-10-31 交互审查 P2-6 + 独立盲审 P2-6:
// 页面从不告诉用户「这台服务器要不要令牌」。/api/health 的 auth_enabled
// 已经在响应里了 —— 复用同一次轮询, 不新增请求。
//
// 三态而不是两态: 探测**失败**时不能默认成「免令牌」—— 那会让用户
// 在需要令牌的服务器上不填令牌, 然后撞上一堆英文 401/403。
function renderAuthBadge(health, reachable = true) {
  if (!authBadge) return;
  if (!reachable || !health) {
    authBadge.textContent = "鉴权状态未知";
    authBadge.className = "auth-badge unknown";
    authBadge.title = "无法连接服务端, 未能确认是否需要 API 令牌";
    return;
  }
  const needed = Boolean(health.auth_enabled);
  authBadge.textContent = needed ? "需要令牌" : "本机免令牌";
  authBadge.className = "auth-badge " + (needed ? "need" : "free");
  authBadge.title = needed
    ? "服务端已开启鉴权 — 提交前请在下方「API 令牌」填入正确令牌"
    : "服务端未开启鉴权 — API 令牌留空即可";
}

function fmtMb(mb) {
  if (mb == null) return "?";
  if (mb >= 1024) return (mb / 1024).toFixed(1) + "G";
  return Math.round(mb) + "M";
}

function renderGpuPill(health) {
  renderAuthBadge(health);
  const gpu = health && health.gpu;
  if (!gpu || !gpu.available) {
    gpuPill.className = "chip-status";
    gpuLabel.textContent = "CPU 模式";
    return;
  }
  const name = gpu.name || gpu.device || "GPU";
  const shortName = name.length > 20 ? name.slice(0, 18) + "…" : name;
  const util = gpu.utilisation_pct != null ? Math.round(gpu.utilisation_pct) : null;
  const vram = gpu.vram_total_mb != null
    ? ` · ${fmtMb(gpu.vram_used_mb)}/${fmtMb(gpu.vram_total_mb)}` : "";
  gpuPill.className = "chip-status ok";
  gpuLabel.textContent = `${shortName}${vram}${util != null ? ` · ${util}%` : ""}`;
}

async function pollGpu() {
  try {
    const r = await fetch("/api/health");
    if (r.ok) renderGpuPill(await r.json());
    else {
      gpuPill.className = "chip-status";
      gpuLabel.textContent = "CPU 模式";
      renderAuthBadge(null, false);
    }
  } catch (_) {
    gpuPill.className = "chip-status";
    gpuLabel.textContent = "离线";
    renderAuthBadge(null, false);
  }
}

pollGpu();

setInterval(pollGpu, 5000);

// ---------------------------------------------------------------------
// API 令牌 — 仅当服务器设置 MEDIASCRIBE_API_TOKEN 时需要。
// HTTP 走 Bearer 头; WebSocket 经 ?token= 传递。默认记住在本机。
// ---------------------------------------------------------------------

export { fmtMb, pollGpu, renderGpuPill };
