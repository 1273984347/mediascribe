// B4 候选 —— Electron 桌面壳。
//
// 形态: Electron 主进程拉起 MediaScribe 的 FastAPI 后端 (sidecar),
//       再用 BrowserWindow 打开 http://127.0.0.1:<port>/。
//
// 基准信号 (stdout 行, 供 bench_shell.py 抓取):
//   WINDOW_READY  —— BrowserWindow did-finish-load (工作台页面已渲染)
//   APP_USABLE    —— 页面内 /api/health 探针成功 (后端确实活着, 页面可交互)
//
// GPU 隔离: 禁用硬件加速 + 给 sidecar 注入 CUDA_VISIBLE_DEVICES=-1,
//            避免与并行的推理层实测争抢显存。

const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const http = require("http");

app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu");

// 离线验证模式: 在 Chromium 层把所有外部域名解析打成 NOTFOUND (真正的浏览器级断网),
// 只放行 127.0.0.1 —— 用来证明工作台在完全断网时仍能起来并可用。
if (process.env.BENCH_OFFLINE === "1") {
  app.commandLine.appendSwitch(
    "host-resolver-rules",
    "MAP * ~NOTFOUND,EXCLUDE 127.0.0.1"
  );
}

const PYTHON = process.env.BENCH_PYTHON;
const PORT = process.env.BENCH_PORT;
const REPO = process.env.BENCH_REPO;
const BASE = `http://127.0.0.1:${PORT}`;

let win = null;
let backend = null;

function mark(tag) {
  process.stdout.write(`${tag} ${Date.now()}\n`);
}

function waitHealth(timeoutMs) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;
    const attempt = () => {
      const req = http.get(`${BASE}/api/health`, (res) => {
        res.resume();
        if (res.statusCode === 200) {
          resolve(true);
        } else {
          retry();
        }
      });
      req.on("error", retry);
      req.setTimeout(2000, () => {
        req.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() > deadline) {
        reject(new Error("backend /api/health timeout"));
        return;
      }
      setTimeout(attempt, 150);
    };
    attempt();
  });
}

function startBackend() {
  backend = spawn(
    PYTHON,
    [
      "-m",
      "uvicorn",
      "mediascribe.web.app:create_app",
      "--factory",
      "--host",
      "127.0.0.1",
      "--port",
      String(PORT),
    ],
    {
      cwd: REPO,
      env: {
        ...process.env,
        CUDA_VISIBLE_DEVICES: "-1",
        MEDIASCRIBE_DEVICE: "cpu",
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    }
  );
  backend.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backend.stderr.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backend.on("exit", (code) => {
    process.stdout.write(`[backend] exited rc=${code}\n`);
  });
}

app.whenReady().then(async () => {
  if (!PYTHON || !PORT) {
    process.stdout.write("FATAL missing BENCH_PYTHON / BENCH_PORT\n");
    app.quit();
    return;
  }
  startBackend();
  try {
    await waitHealth(120000);
  } catch (err) {
    process.stdout.write(`FATAL backend not ready: ${err.message}\n`);
    app.quit();
    return;
  }

  win = new BrowserWindow({
    width: 1280,
    height: 900,
    show: true,
    title: "MediaScribe",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.webContents.on("did-finish-load", async () => {
    mark("WINDOW_READY");
    try {
      const ok = await win.webContents.executeJavaScript(
        "fetch('/api/health').then(r => r.ok).catch(() => false)"
      );
      if (ok) {
        mark("APP_USABLE");
      } else {
        process.stdout.write("APP_USABLE_FAILED in-page health probe returned false\n");
      }
    } catch (err) {
      process.stdout.write(`APP_USABLE_FAILED ${err.message}\n`);
    }
  });

  win.on("closed", () => {
    win = null;
  });

  await win.loadURL(`${BASE}/`);
});

app.on("window-all-closed", () => {
  if (backend && !backend.killed) {
    backend.kill();
  }
  app.quit();
});

app.on("before-quit", () => {
  if (backend && !backend.killed) {
    backend.kill();
  }
});
