# L2 工作台壳 / 交付形态层 —— 可复跑基准实测

本目录是 **L2 层**（工作台以什么形态交付给用户）的实测基准，回答五个候选形态的
**冷启动 / 驻留内存 / 分发体积 / 离线自包含 / 跨平台可移植性**。

所有数字均在本机（Windows 11 / i7-13650HX / RTX 4060 Laptop / 16 GB）真跑得出，
原始数据在 [`results.json`](./results.json)，**禁止**用估算值覆盖。

---

## 1. 候选与实测状态一览

| 候选 | 形态 | 状态 | 冷启动中位数 | 驻留内存 | 分发体积 | 离线自包含 |
|---|---|---|---|---|---|---|
| **B1** `web-local` | FastAPI + uvicorn + 浏览器开 localhost（现状） | ✅ 本机实测 | **3760.8 ms** | **443,305,984 B (422.8 MiB)** / 1 进程 | **无独立分发物**（需 Python 环境）；仓内静态页 57,016 B | ✅ 是 |
| **B2** `web-static` | FastAPI 托管 Vite+React 预构建壳 | ✅ 本机实测 | **3966.8 ms** | **442,720,256 B (422.2 MiB)** / 1 进程 | **151,310 B (0.14 MiB)** `dist/` | ✅ 是 |
| **B3** `tauri` | Tauri 2 桌面壳（Rust + sidecar） | ❌ **本机不可测** | 未测（缺 MSVC） | 未测 | 未测（本地未产出安装包） | 未测 |
| **B4** `electron` | Electron 桌面壳 + FastAPI sidecar | ✅ 本机实测 | **5059.2 ms** | **806,481,920 B (769.1 MiB)** / 6 进程 | **81,695,273 B (77.9 MiB)** NSIS 安装包（`dist/` 合计 362,855,971 B） | ✅ 是 |
| **B5** `browser-wasm` | 纯浏览器 + whisper.cpp WASM | ❌ **本机不可测（推理）** | 未测 | 未测 | **79,508,332 B (75.83 MiB)**（本机实测下载所得） | ❌ 未通过（阻断点非网络） |

> 逐候选的冷启动原始 3 次值、内存进程明细、跨平台来源 URL、踩坑原文，都在 `results.json`
> 对应条目里；本 README 只做汇总与口径说明。

---

## 2. 测量口径

| 指标 | 口径 | 实现 |
|---|---|---|
| **冷启动** | 进程 spawn 瞬间 → 首个「服务健康检查 200 / 窗口 ready 标记」的墙钟毫秒（`time.perf_counter`）。每次独立起进程，跑 **3 次取中位数**，原始值全留。 | B1/B2: `GET /api/health` 首次 200；B4: Electron 主进程 stdout 的 `APP_USABLE` 标记（窗口 `did-finish-load` **且** 页内 `/api/health` 探针成功）；B5: `APP_USABLE` 等价信号 = WASM 引擎 `instance` 就绪。 |
| **驻留内存** | **Windows 口径工作集**：`GetProcessMemoryInfo().WorkingSetSize`（psutil `memory_info().rss` 在 Windows 上即此值）。取**进程树**（父进程 + 全部递归子进程）求和。稳定期先 `settle` 再连采 3 次取中位数；另记 `peak_wset`。 | 采样时刻写在 `results.json` 的 `memory.method` / `memory.samples_bytes` 里。 |
| **分发体积** | 构建产物磁盘字节数（`os.walk` 递归求和）。无独立分发物的候选明确记「需 Python 环境，不产出独立分发物」。 | B1: `无`；B2: `dist/`；B4: NSIS `.exe` 安装包 + `win-unpacked/`；B5: 浏览器侧静态文件。 |
| **离线自包含** | 能否在**完全断网**下完成启动与核心功能。验证方式见下。 | 两条独立证据：①环境变量级断网——`HTTP(S)/ALL_PROXY=http://127.0.0.1:9`（连接必失败）；②`netstat -ano` 每 50 ms 快照，断言被测进程树**无非回环 TCP 连接**。B4/B5 额外加**浏览器/Chromium 级断网** `--host-resolver-rules='MAP * ~NOTFOUND,EXCLUDE 127.0.0.1'`。 |
| **跨平台可移植性** | **以官方文档/仓库事实为依据**，逐条给出来源 URL（`results.json` → `cross_platform.sources`）。不靠猜。 | 见 §5。 |

### GPU 隔离（重要）

本基准与另一条并行的**推理层实测**同机运行（那条线用 `--devices cuda`）。为避免显存/资源
冲突污染数据，**所有被测子进程**都注入：

```
CUDA_VISIBLE_DEVICES=-1        # 实测: 空字符串不够, torch.cuda.is_available() 仍为 True
MEDIASCRIBE_DEVICE=cpu
```

实测 `CUDA_VISIBLE_DEVICES=""` 时 `torch.cuda.is_available()=True`，而 `/api/health` 的
`gpu_health()` 会调 `torch.cuda.current_device()` 建立 CUDA 上下文占显存；用 `-1` 后
`is_available()=False, device_count()=0`，本基准**全程不占显存**（见 B1 健康检查返回
`"gpu":{"available":false}`）。本基准只做壳层与轻量后端健康检查，**不做任何推理**。

---

## 3. 复跑方式

```bash
# 解释器必须是装了 fastapi/uvicorn/psutil 的那个（WindowsApps 商店 stub 不可用）
PY="C:\Users\12739\AppData\Local\Programs\Python\Python312\python.exe"

$PY scripts/bench/shell/bench_shell.py b1 --runs 3   # FastAPI + uvicorn（现状）
$PY scripts/bench/shell/bench_shell.py b2 --runs 3   # Vite+React 预构建壳（会跑 npm install + vite build）
$PY scripts/bench/shell/bench_shell.py b4 --runs 3   # Electron（会跑 npm install + electron-builder）
$PY scripts/bench/shell/bench_shell.py b3            # Tauri（只做环境探测 + 最小链接测试）
$PY scripts/bench/shell/bench_shell.py b5 --runs 1   # 纯浏览器 WASM
$PY scripts/bench/shell/bench_shell.py all --runs 3  # 全跑
```

常用开关：

| 开关 | 作用 |
|---|---|
| `--runs N` | 冷启动重复次数（默认 3） |
| `--skip-build` | 跳过 `npm install` / `vite build` / `electron-builder`，复用 `output/bench/shell/` 里的已有产物 |
| `--skip-tauri-build` | 跳过完整 Tauri 编译，只用**最小链接测试**判定阻断原因（秒级） |

### 目录结构

```
scripts/bench/shell/
├── bench_shell.py          # 测量驱动（唯一入口）
├── results.json            # 各候选原始数据 + 口径元信息 + 环境信息
├── README.md               # 本文件
└── apps/                   # 各候选的脚手架源码（入库）
    ├── b2-react/           # Vite + React 等价最小工作台壳 + FastAPI 静态托管层
    │   ├── package.json  vite.config.mjs  index.html
    │   ├── src/            # main.jsx / App.jsx / styles.css
    │   └── server.py       # 薄 ASGI 路由器: 静态壳在前, 其余路径原样转发给 mediascribe.web.app
    ├── electron/            # main.js (拉起 sidecar + 开窗口) + package.json (electron-builder)
    ├── tauri/               # src-tauri/ (Cargo.toml / tauri.conf.json / main.rs) + make_icons.py
    └── b5-wasm/             # fetch_assets.py / drive.mjs (CDP 驱动) / diag_wasm.py / build_wasm.py
output/bench/shell/        # 构建产物（被 .gitignore 忽略, 不入库）
```

---

## 4. 各候选「不可测」的具体原因（不留空、不填猜测值）

### B3 `tauri` —— 本机不可编译：缺 Microsoft Visual C++ Build Tools

* 缺失组件（**具体到组件名**）：**MSVC 链接器 `link.exe`**、Visual Studio / Build Tools、
  Windows SDK、`cl.exe`。本机探测结果：`vswhere.exe` 不存在、
  `C:\Program Files\Microsoft Visual Studio` 与 `C:\Program Files (x86)\Microsoft Visual Studio` 不存在、
  `C:\Program Files (x86)\Windows Kits\10` 不存在、`cl.exe` 不在 PATH、`link.exe` 不在 PATH。
* **Rust 工具链本身是装好了的**（`rustc 1.99.1 (58cf8f9c3 2026-10-13)`，经 rustup 官方渠道），
  WebView2 Runtime 也在（`155.0.4283.62`）—— 唯一缺的就是 MSVC。
* 最小链接测试（`cargo build --release` 一个空 `fn main()`）**确定性失败**，原文：

```
   Compiling b3linktest v0.1.0 (D:\MediaScribe\output\bench\shell\b3-linktest)
error: linker `link.exe` not found
  |
  = note: program not found

note: the msvc targets depend on the msvc linker but `link.exe` was not found

note: please ensure that Visual Studio 2017 or later, or Build Tools for Visual Studio were installed with the Visual C++ option

note: VS Code is a different product, and is not sufficient

error: could not compile `b3linktest` (bin "b3linktest") due to 1 previous error
```

  退出码 **101**。完整 Tauri 构建需要数百个 crate、耗时很长，本轮按指令未跑完
  （`results.json` 里 `cargo_build_skipped` 已注明），但阻断点已由上面这条最小链接测试锁定。
* **分发体积未在本机产出**。公开数据补齐见 §5，**非本机实测**。

### B5 `browser-wasm` —— 权重可得，但官方预构建 WASM 在当前 Chrome 上实例化失败

这里要**修正任务书的前提**：`huggingface.co` 主站确实不可达，但 **`hf-mirror.com` 可达**，
权重能完整下载（`ggml-tiny.bin` = **77,691,713 B**，与官方 `models/README.md` 的
"74MB for the tiny model" 吻合）。所以 B5 不是「权重取不到」。

真正的阻断点是**官方预构建的 WASM 包在本机 V8 上跑不起来**：

* 页面原文（Chrome 155 / headless）：
  `failed to asynchronously prepare wasm: CompileError: WebAssembly.instantiate(): unknown type form: 106 @+12`
  随后 `Uncaught TypeError: Module.init is not a function`（`index.html:624`）。
* 同一二进制在 Node 24（同样 V8）单独编译也失败：
  `CompileError: section was shorter than expected size (151363 bytes expected, 1246 decoded) @+1258`。
  而 `scripts/bench/shell/apps/b5-wasm/diag_wasm.py` 解出的**段表本身自洽**
  （type section `id=1 size=151363` 位于偏移 12）—— 即该产物与当前 V8 不兼容。
* `whisper.cpp` GitHub 最新几个 release（`v1.9.5` / `b5454` / `b5130`）**没有 wasm 资产**，
  只有原生二进制。
* **数据通路本身是通的**（实测，页面输出）：
  `storeFS: stored model: whisper.bin size: 77691713`、
  `js: audio loaded, size: 176000` —— 模型入库与音频解码都成功，**只卡在 WASM 引擎实例化**。
* 官方自建路径需要 Emscripten(emsdk) + CMake + 源码，本机三者均缺；按本轮指令**未执行自建**。
* 因此 **B5 的冷启动 / 驻留内存留空未测**（宁缺勿猜），**分发体积是本机实测**
  （79,508,332 B，把官方预构建包与权重真实下载下来量的）。

---

## 5. 跨平台可移植性（官方来源，**非本机实测**）

| 候选 | 官方结论 | 来源 URL |
|---|---|---|
| B1 / B2 | 形态是「本机起 HTTP 服务 + 任意浏览器打开」，壳层零平台依赖；平台差异只落在 Python 依赖与 ffmpeg 二进制上。B2 额外只多一个**构建期** Node.js 依赖，运行期不需要。 | https://fastapi.tiangolo.com/advanced/static-files/ · https://vite.dev/guide/ |
| B3 | 官方支持 **Windows 7+ / macOS Catalina 10.15+ / Linux**（需 `libwebkit2gtk-4.1-dev`、`build-essential`、`libxdo-dev` 等）。**Windows 上官方明确要求 Microsoft C++ Build Tools + Edge WebView2**（原文：*"Tauri uses the Microsoft C++ Build Tools for development as well as Microsoft Edge WebView2. These are both required for development on Windows."*）。打 MSI 还需 Windows 的 VBSCRIPT 可选功能。 | https://v2.tauri.app/start/prerequisites/ · https://v2.tauri.app/distribute/ |
| B4 | 官方原文：*"Electron allows you to create cross-platform apps that work on Windows, macOS, and Linux with a single JavaScript codebase."* 打包工具 Electron Forge 按平台产出对应分发物（Windows 安装包 / macOS `.app`、DMG / Linux `deb`）。macOS 分发需代码签名与公证。 | https://www.electronjs.org/docs/latest/tutorial/tutorial-prerequisites · https://www.electronjs.org/docs/latest/tutorial/tutorial-packaging · https://www.electronforge.io/config/makers |
| B5 | 平台支持面 = 浏览器支持面：需支持 **WASM SIMD 128-bit intrinsics** 的浏览器（Chrome 91+/Edge 91+/Firefox 89+/Safari 16.4+）。官方限制：音频最长 120 秒、模型上限 `small`、仅 Greedy 采样。 | https://github.com/ggml-org/whisper.cpp/tree/master/examples/whisper.wasm · https://webassembly.org/roadmap/ |

**Tauri 分发体积（公开数据补齐，非本机实测）**：本机因缺 MSVC 未产出安装包。
以官方发布页为准：https://github.com/tauri-apps/tauri/releases 。
Tauri 官方文档另有「App Size」章节给出各端体积口径：
https://v2.tauri.app/concept/size/ （**注意：本轮未逐项抓取该页数值，表中不填具体字节数**，
避免给出未经验证的数字）。

---

## 6. 踩坑记录（错误原文）

### B1 —— 撞到两个**既有代码缺陷**（本次未改动任何既有代码，只如实记录）

1. 文档与 README 声明的启动方式 `python -m mediascribe.web.app --port 8000`
   **必然失败**：`mediascribe/web/app.py:1242` 的 `if __name__ == "__main__": sys.exit(main())`
   位于各 `_register_*_routes` 定义之前（首个在 `app.py:1246`），`main()` 执行时这些函数尚未定义。
   原文：
   ```
   File "D:\MediaScribe\mediascribe\web\app.py", line 1206, in create_app
       _register_health_routes(app)
   NameError: name '_register_health_routes' is not defined
   ```
2. `scripts/launch/one_click_up.py` 的 `launch_local` 用 `python -m uvicorn mediascribe.web.app:app`，
   但 `mediascribe/web/app.py` **没有模块级 `app` 对象**（只有 `create_app` 工厂函数）。原文：
   ```
   ERROR:    Error loading ASGI app. Attribute "app" not found in module "mediascribe.web.app".
   ```
3. **本基准采用的可用方式**（不改任何既有代码）：
   `python -m uvicorn mediascribe.web.app:create_app --factory --host 127.0.0.1 --port N` → 健康检查 200。
4. 无鉴权模式启动即打印告警（按现有代码默认行为处理）：
   `MEDIASCRIBE_API_TOKEN 未配置 — API 处于无鉴权模式, 仅信任本机/内网来源(公网来源会被拒绝)。`
5. `CUDA_VISIBLE_DEVICES=""` 屏蔽不掉 GPU（详见 §2）。
6. Windows `SO_REUSEADDR` 允许多个 `http.server` 同时「监听」同一端口，基准脚本必须自选独占端口
   （本轮实测被别的进程的 http.server 抢过端口，取到错误页面）。

### B2

1. 用用户级共享 npm 缓存时 `npm install` 随机失败，原文：
   ```
   npm error code EEXIST
   npm error syscall rename
   npm error ENOENT: no such file or directory, rename 'C:\Users\12739\AppData\Local\npm-cache\_cacache\tmp\8a3a0237' -> 'C:\Users\12739\AppData\Local\npm-cache\_cacache\content-v2\sha512\a3\96\...'
   npm error File exists: C:\Users\12739\AppData\Local\npm-cache\_cacache\content-v2\sha512\a3\96\...
   ```
   → 改用 bench 专属缓存目录 `npm install --cache <dir>` 即可复现地避开。
2. 连带效应：npm install 失败后 `npm run build` 报 `'vite' is not recognized as an internal or external command`
   —— 报错点与真因不在一处，排查要看 npm install 的 rc。
3. **零构建 vs 预构建的实测差异极小**：B2 只换前端交付方式，冷启动中位数 3966.8 ms vs B1 3760.8 ms
   —— 冷启动被 Python 侧主导，与前端无关。

### B3

1. rustup 首次安装时最后一个组件（rust-std）的 `.partial` 下载长时间停在 0 字节，留下
   `missing manifest in toolchain 'stable-x86_64-pc-windows-msvc'`；重跑 `rustup-init` 后 rustup 自己
   打印 `info: recovering from a partially installed toolchain` 并修复成功。
2. 两个 `rustup-init` 进程并发写 `~/.rustup` 会互相踩，需先结束旧进程。
3. 缺 MSVC 的确定性报错见 §4（`error: linker 'link.exe' not found`）。
4. 按任务约束**未安装来历不明的工具链**去绕过该阻断。

### B4

1. Electron 首次安装需下载约 110–150 MB 二进制。本机**直连 GitHub Releases 只有 ~47 KB/s**，
   改 `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`（官方支持的覆盖变量）后正常。
2. `npm install` 若在 electron postinstall 阶段被中断，会留下 `node_modules/electron` 但缺
   `dist/electron.exe`；此时再跑 `npm install` 会报 `up to date in 7s` 并**跳过 postinstall**，
   导致 `electron.exe` 永远缺失 —— 必须删掉残缺的包目录强制重跑。
3. `electron-builder` 解压 `winCodeSign` 需要创建符号链接，本机无「创建符号链接」特权：
   ```
   ERROR: Cannot create symbolic link : 客户端没有所需的特权。 : C:\Users\12739\AppData\Local\electron-builder\Cache\winCodeSign\...\darwin\10.12\lib\libcrypto.dylib
   ```
   → 用 `win.signAndEditExecutable: false` 绕开（本基准的安装包因此不带 exe 资源元数据）。
4. Electron 会派生 **主进程 + GPU + utility + renderer** 多个进程，内存必须按**进程树**求和，
   单看主进程会低估到 1/4 以下。
5. 为避开与并行推理实测争抢显存，Electron 侧调了 `app.disableHardwareAcceleration()` +
   `--disable-gpu`（渲染走 SwiftShader/CPU）—— 内存数字按此口径理解。
6. 正常联网跑时，Electron 的 Chromium 网络栈会在启动时向 `114.250.64.33 / 114.250.66.33 /
   114.250.70.4 / 114.250.64.34`（本机所在网络的运营商/加速器地址；本机 DNS 指向
   `192.168.1.1` / `192.168.101.1`，无系统代理）建立 TCP 连接。**这些不是功能依赖** ——
   加浏览器级断网后重跑，窗口照样渲染完成且后端 200，出站连接数为 **0**。

### B5

1. `huggingface.co` 不可达、**`hf-mirror.com` 可达**（与任务书前提相反）。
2. 官方预构建 WASM 在 Chrome 155 / Node 24 上实例化失败（原文见 §4）。
3. `whisper.cpp` GitHub Releases 无 wasm 资产。
4. demo 页**两个 file input 的 `name` 都是 `file`**（模型 `#whisper-file` 与音频输入），
   按 `name` 选会选错（把音频写进模型输入框），必须按 `onchange="loadAudio(event)"" 精确定位。
5. 自建路径（emsdk + cmake + 源码）本机三者均缺，本轮按指令未执行。

---

## 7. 局限与已知偏差（务必随数据一起读）

* **内存数字受同机并行负载影响**：本轮同机还跑着另一条 `--devices cuda` 的 ASR 推理实测，
  系统空闲内存一度只剩 1.5–2.4 GB。B1/B2 的 422 MiB 属单进程、波动 ±1% 量级；
  B4 的 769 MiB 含 Python sidecar 423 MiB。
* **B4 的冷启动方差大**：3 次原始值 5059.2 / 3596.5 / 5475.7 ms（极差 ~1.5 s），
  因为 Electron 首启要拉起 4 个进程并等后端 ~3.7 s。表格取中位数，请同时看原始值。
* **B1/B2 的「冷启动」不含浏览器自身启动与页面渲染**（那部分归推理层/前端层）。
  B4 的口径可比（`APP_USABLE` = 窗口渲染完 + 后端健康）。
* **B4 的安装包**用 `signAndEditExecutable: false` 打出，体积与正式签名版会有差异。
* **B5 未做浏览器内推理**，因此其「冷启动」「驻留内存」留空；分发体积虽为本机实测，
  但那是**官方预构建包**的体积，不是自建产物的体积。
* `results.json` 里所有 `null` 都表示**该指标在本机不可测**，没有任何一项是估算值。
