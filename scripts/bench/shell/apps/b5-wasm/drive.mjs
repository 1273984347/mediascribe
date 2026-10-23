// B5 候选 —— 用 Chrome DevTools Protocol 驱动 headless Chrome 实跑 whisper.cpp WASM 推理。
//
// 不引入任何 npm 依赖: Node 22+ 自带全局 WebSocket 与 fetch, 直接讲 CDP。
//
// 阶段与信号 (对应 whisper.wasm demo 页的真实实现):
//   T0  浏览器进程 spawn
//   T1  页面 ready           —— document.readyState === 'complete' 且 main.js 已加载
//   T2  模型入 FS           —— DOM.setFileInputFiles(#whisper-file) -> loadFile -> storeFS('whisper.bin')
//   T3  音频解码完成         —— loadAudio -> AudioContext.decodeAudioData -> 全局 audio
//   T4  引擎 ready          —— onProcess(false) 返回后 instance 已创建 (WASM 引擎 + 模型就绪)
//   T5  出结果              —— #output 文本框出现转写文本
//
// 输出: 一行 JSON 到 stdout。

import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function arg(name, dflt) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : dflt;
}

const URL_ = arg("url", "http://127.0.0.1:8123/index.html");
const CHROME = arg("chrome", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe");
const MODEL = arg("model", "");
const AUDIO = arg("audio", "");
const OUT = arg("out", "");
// 离线模式: 把所有非本地域名的 DNS 解析打成 NOTFOUND (真正的浏览器级断网)
const OFFLINE = process.argv.includes("--offline");
const TIMEOUT = Number(arg("timeout", "300")) * 1000;

const t0 = Date.now();
const elapsed = () => Date.now() - t0;

const userDataDir = mkdtempSync(join(tmpdir(), "b5-chrome-"));
const args = [
  "--headless=new",
  "--remote-debugging-port=0",
  `--user-data-dir=${userDataDir}`,
  "--no-first-run",
  "--no-default-browser-check",
  "--disable-gpu",              // 与推理实测隔离, 不占显存
  "--disable-dev-shm-usage",
  "--disable-background-timer-throttling",
  "--disable-renderer-backgrounding",
  "--mute-audio",
  "--autoplay-policy=no-user-gesture-required",
  "--js-flags=--max-old-space-size=2048",
];
if (OFFLINE) {
  // 浏览器级断网: 外部域名一律解析失败, 仅放行本机
  args.push("--host-resolver-rules=MAP * ~NOTFOUND,EXCLUDE 127.0.0.1");
}
args.push(URL_);

const chrome = spawn(CHROME, args, { stdio: ["ignore", "pipe", "pipe"] });

let stderrBuf = "";
let wsUrl = null;
chrome.stderr.on("data", (d) => {
  stderrBuf += d.toString();
  const m = stderrBuf.match(/DevTools listening on (ws:\/\/\S+)/);
  if (m && !wsUrl) {
    wsUrl = m[1];
  }
});
chrome.stdout.on("data", () => {});

function fail(reason, extra = {}) {
  const payload = { ok: false, reason, elapsed_ms: elapsed(), ...extra };
  writeFileSync(OUT, JSON.stringify(payload, null, 2));
  console.log(JSON.stringify(payload));
  try { chrome.kill("SIGKILL"); } catch {}
  process.exit(1);
}

async function waitForWs() {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (wsUrl) return wsUrl;
    await new Promise((r) => setTimeout(r, 100));
  }
  fail("DevTools websocket 未出现", { stderr_tail: stderrBuf.slice(-1500) });
}

async function findPageTarget() {
  const port = new URL(wsUrl).port;
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await r.json();
      const page = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await new Promise((r) => setTimeout(r, 200));
  }
  fail("找不到 page target");
}

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message} (${JSON.stringify(msg.error.data ?? "")})`));
        else resolve(msg.result);
      } else if (msg.method) {
        this.events.push(msg);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP 超时: ${method}`));
        }
      }, TIMEOUT);
    });
  }
  async evaluate(expression, awaitPromise = false) {
    const r = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise,
    });
    if (r.exceptionDetails) {
      throw new Error(`页面异常: ${r.exceptionDetails.text} ${r.exceptionDetails.exception?.description ?? ""}`);
    }
    return r.result?.value;
  }
  async waitFor(expr, timeoutMs, label) {
    const deadline = Date.now() + timeoutMs;
    let last = null;
    while (Date.now() < deadline) {
      try {
        last = await this.evaluate(expr);
        if (last) return last;
      } catch (e) {
        last = String(e.message).slice(0, 200);
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    throw new Error(`等待超时: ${label} (最后=${JSON.stringify(last)})`);
  }
}

async function main() {
  await waitForWs();
  const pageWs = await findPageTarget();
  const ws = new WebSocket(pageWs);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", (e) => reject(new Error("WS 连接失败")), { once: true });
  });
  const cdp = new CDP(ws);
  globalThis.__cdp = cdp;
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("DOM.enable");

  // T1: 页面 ready
  await cdp.waitFor(
    "document.readyState === 'complete' && typeof window.onProcess === 'function'",
    60000,
    "页面脚本就绪"
  );
  const t_page_ready = elapsed();

  // T2: 模型文件 -> FS
  const root = await cdp.send("DOM.getDocument", { depth: -1 });
  const modelNode = await cdp.send("DOM.querySelector", {
    nodeId: root.root.nodeId,
    selector: "#whisper-file",
  });
  if (!modelNode.nodeId) fail("未找到 #whisper-file 输入框");
  await cdp.send("DOM.setFileInputFiles", { files: [MODEL], nodeId: modelNode.nodeId });
  const t_model_set = elapsed();
  // storeFS 是同步的 (FileReader onload 异步), 等 whisper.bin 真正进 Emscripten FS
  await cdp.waitFor(
    "(function(){ try { return typeof FS !== 'undefined' && FS.readdir('/').indexOf('whisper.bin') >= 0; } catch (e) { return false; } })()",
    120000,
    "模型进入 Emscripten FS"
  );
  const t_model_loaded = elapsed();

  // T3: 音频
  // 注意: 页面里 **两个** file input 的 name 都是 "file" (模型 #whisper-file 与音频输入),
  // 必须按 onchange 精确定位音频输入, 否则会把音频写进模型输入框。
  const audioNode = await cdp.send("DOM.querySelector", {
    nodeId: root.root.nodeId,
    selector: 'input[type="file"][onchange="loadAudio(event)"]',
  });
  if (!audioNode.nodeId) fail("未找到音频文件输入框");
  await cdp.send("DOM.setFileInputFiles", { files: [AUDIO], nodeId: audioNode.nodeId });
  await cdp.waitFor("typeof audio !== 'undefined' && audio !== null && audio.length > 0", 60000, "音频解码完成");
  const t_audio_ready = elapsed();

  // T4: 引擎 ready —— onProcess 内部同步完成 Module.init('whisper.bin')
  const t_before = elapsed();
  await cdp.evaluate("onProcess(false)");
  const inst = await cdp.evaluate("!!instance");
  if (!inst) fail("whisper 实例未创建", { output_tail: await cdp.evaluate("document.getElementById('output').value.slice(-800)") });
  const t_engine_ready = elapsed();

  // T5: 出结果
  await cdp.waitFor(
    "(() => { const v = document.getElementById('output').value; return v && v.length > 0 && !v.includes('processing - this might take a while') ? v : null; })()",
    240000,
    "转写结果"
  );
  const t_result = elapsed();
  const transcript = await cdp.evaluate("document.getElementById('output').value");

  const payload = {
    ok: true,
    offline: OFFLINE,
    timings_ms: {
      t_page_ready: t_page_ready,
      t_model_set: t_model_set,
      t_model_loaded: t_model_loaded,
      t_audio_ready: t_audio_ready,
      t_engine_ready: t_engine_ready,
      t_result: t_result,
    },
    cold_start_ms: t_engine_ready,   // 浏览器进程启动 -> WASM 引擎 + 模型就绪
    transcription_ms: t_result - t_before,
    total_ms: t_result,
    transcript_tail: String(transcript).slice(-400),
    chrome_pid: chrome.pid,
    url: URL_,
  };
  writeFileSync(OUT, JSON.stringify(payload, null, 2));
  console.log(JSON.stringify(payload));
  const hold = Number(arg("hold", "0")) * 1000;
  if (hold > 0) {
    // 保持浏览器存活, 供外部测量驻留内存
    await new Promise((r) => setTimeout(r, hold));
  }
  try { chrome.kill("SIGKILL"); } catch {}
  process.exit(0);
}

main().catch(async (e) => {
  // 失败时把页面状态 (textarea 输出 + console 错误) 一并带出, 便于定位
  let pageState = null;
  try {
    pageState = globalThis.__cdp ? await globalThis.__cdp.evaluate(
      "document.getElementById('output') ? document.getElementById('output').value.slice(-1200) : 'no #output'"
    ) : null;
  } catch {}
  fail(e.message, { stack: String(e.stack).slice(0, 800), page_output: pageState });
});
