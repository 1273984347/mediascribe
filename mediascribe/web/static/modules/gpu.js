// GPU 状态指示：轮询 /api/health，每 5 秒刷新。
import { gpuLabel, gpuPill } from './dom.js';

function fmtMb(mb) {
  if (mb == null) return "?";
  if (mb >= 1024) return (mb / 1024).toFixed(1) + "G";
  return Math.round(mb) + "M";
}

function renderGpuPill(health) {
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
    else { gpuPill.className = "chip-status"; gpuLabel.textContent = "CPU 模式"; }
  } catch (_) {
    gpuPill.className = "chip-status";
    gpuLabel.textContent = "离线";
  }
}

pollGpu();

setInterval(pollGpu, 5000);

// ---------------------------------------------------------------------
// API 令牌 — 仅当服务器设置 MEDIASCRIBE_API_TOKEN 时需要。
// HTTP 走 Bearer 头; WebSocket 经 ?token= 传递。默认记住在本机。
// ---------------------------------------------------------------------

export { fmtMb, pollGpu, renderGpuPill };
