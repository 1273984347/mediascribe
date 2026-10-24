// 鉴权：API token 的存取、请求头与 WebSocket 连接串构造。
import { rememberToken, tokenInput } from './dom.js';

function apiToken() { return tokenInput.value.trim(); }

function authHeaders() {
  const t = apiToken();
  return t ? { "Authorization": "Bearer " + t } : {};
}

function tokenHeaderSafe(t) {
  // RFC 7230 token 字符集: 子协议头只接受这种字符; 含空格/非 ASCII 的
  // 自定义 token 会让浏览器在 new WebSocket() 时直接抛错。
  return /^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$/.test(t);
}

function wsUrl(path) {
  const t = apiToken();
  // 默认走子协议通道(?token= 会落 uvicorn/反代访问日志); 字符集不安全
  // 时降级回 query 通道(2026-10-03 审查加固 + review 修复)。
  const q = t && !tokenHeaderSafe(t) ? "?token=" + encodeURIComponent(t) : "";
  return (location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + path + q;
}

function wsProtocols() {
  const t = apiToken();
  return t && tokenHeaderSafe(t) ? [t] : undefined;
}

// ---------------------------------------------------------------------
// 提交 → POST /api/jobs(异步任务 + 取消 + WS 进度)
// ---------------------------------------------------------------------

export { apiToken, authHeaders, tokenHeaderSafe, wsProtocols, wsUrl };
