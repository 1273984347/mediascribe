# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (2026-10-25 Web 层职责拆分：app.py 2088 → 515 行)

执行记录见 [`docs/reports/T03-Web-层职责拆分-任务计划执行与经验沉淀报告-2026-10-25.md`](docs/reports/T03-Web-层职责拆分-任务计划执行与经验沉淀报告-2026-10-25.md)。

把 `mediascribe/web/app.py`（原 2088 行 / 92 KB，仓库最大单文件）按职责拆成
**19 个子模块**，app.py 只剩装配层（515 行，−75%）。**本次拆分本身是纯重构，
对外行为零变化**（同批次的 T02 另有刻意的行为变更，已单列于下）。

- **辅助逻辑层（12 个）** —— `logging_setup` / `public_url` / `cors` / `static_assets` /
  `validation` / `security` / `ratelimit` / `gpu_health` / `job_runner` / `ws_bridge` /
  `models` / `pipeline_bridge`。
- **路由层（7 个）** —— `route_health` / `route_extension` / `route_pages` / `route_wiki` /
  `route_transcribe` / `route_ws` / `route_jobs`。
- **函数体逐字搬移**（AST 定位区间 + 脚本切片，不手抄）⇒ 逻辑零改动。
- **子模块 import 全部用绝对路径** `from mediascribe.web.x import Y` —— 测试同时用
  `import app`（裸模块）与 `import mediascribe.web.app`（包路径）两条路径加载，
  相对导入会让两条路径各拿一份子模块实例 ⇒ 类对象 identity 分叉。
- **`_build_pipeline` 改为经 `_pipeline_factory` 间接注入** —— 它有测试 patch 目标
  （`mock.patch("app._build_pipeline")`），且测试顺序是「先 `create_app` 再 patch」，
  直接传参会在装配期捕获原函数 ⇒ patch 失效；工厂在**调用时**才查模块全局，
  patch 语义与拆分前一致。
- `app.py` 保留全部历史公共名字的 **re-export**（`__all__` 列 55 个），
  `from app import X` 的用法不受影响。
- ⚠️ **可观察变化**：日志来源名从 `mediascribe.web.app` 细化为
  `mediascribe.web.<子模块>`（定位更准，但运维上可见）。
- **可选依赖契约修复** —— 独立盲审实测发现：fastapi 缺失时，7 个 route 模块顶层
  `from fastapi import …` 会让 `import mediascribe.web.app` 直接 ImportError，
  `create_app()` 的 `RuntimeError("FastAPI is not installed…")` 成了死代码；
  `security.py` 的兜底把 `Header` 置 `None`，而 `Header(default=None)` 在**默认
  参数**位置、函数定义时求值 ⇒ 兜底自身就抛 `TypeError`。
  ⇒ 路由模块改用**惰性 `Depends` 包装**，路由导入纳入 `_FASTAPI_AVAILABLE` 条件。
  新增 `tests/test_web_optional_deps.py` 锁死该契约（子进程屏蔽 fastapi/pydantic/
  starlette 后逐个 import 全部 21 个 web 模块，并断言 `create_app()` 抛
  `RuntimeError`）。

### Fixed (2026-10-25 Web UI 版本号收敛 + 前端质量修复)

执行记录见 [`docs/reports/T02-Web-UI-版本号收敛与前端质量修复-任务计划执行与经验沉淀报告-2026-10-25.md`](docs/reports/T02-Web-UI-版本号收敛与前端质量修复-任务计划执行与经验沉淀报告-2026-10-25.md)。

- **版本号收敛为单一真源** —— 同一产品曾在三处各说各话：`/api/health` 报 `3.2.0c`、
  页脚写 `v3.3.0`、包真源是 `3.3.0`。新增零依赖的 `mediascribe/_version.py` 持有
  `__version__`；`FastAPI(version=…)` 与 `/api/health` 改用同一常量，页脚版本号改为
  后端注入 `{{APP_VERSION}}`（模板不再硬编码字面量）。
  ⇒ 连带修掉一条**把旧漂移值固化成期望值的旧测试**（`test_health_reports_v320c_version`），
  并新增 `tests/test_version_consistency.py`（6 条）锁死「真源 → 各出口」，含防回退断言。
- **首屏整块白闪** —— 入场动画的 `animation-fill-mode: both` 叠加 `nth-child` 延迟链，
  把主体钉在首帧 `opacity:0`，表现为刷新时白闪一下。去掉延迟链、时长 0.4s → 0.25s、
  `from` 帧保留 0.35 透明度 ⇒ 实测 229ms 即完整渲染（改前 312–383ms 整块全白）。
- **次级文字对比度修到 WCAG AA** —— `.hint` 与 `::placeholder` 用的 `--fg-3` 在浅色主题
  只有 2.76:1、**默认的暗色主题**只有 2.35:1。浅色 `--fg-3` 0.38 → 0.55（4.8:1）、
  `--fg-2` 0.56 → 0.66（7.3:1，保住三级文字层级），暗色 `--fg-3` 0.26 → 0.48（4.9:1）。
- **静态资源不再被浏览器缓存** —— Starlette 的 `StaticFiles` 不发 `Cache-Control`，
  改完前端样式页面仍是旧的（截图字节数与改前完全相同）。改为两层：页面引用带
  `?v=<文件 mtime 毫秒>`（后端注入）+ `/static/*` 响应带 `no-cache, must-revalidate`。
  ⇒ 第二层不可省：ES module 内部 `import "./modules/x.js"` 路径是静态写死的，query 根本管不到。
  新增 `TestStaticCacheControl`（4 条）覆盖两条路径。

### Added (2026-10-25 Web UI 单文件离线预览版)

新增 `scripts/tools/build_standalone_web.py` —— 把 Web UI 打包成**自包含的单文件
HTML**（双击即开，无需服务端、无需构建）。

- **ES module 图 → IIFE 闭包序列** —— `import { a } from './x.js'` → `const { a } = __mod_x;`、
  `export { a, b }` → `return { a, b };`，按依赖拓扑序 `dom → theme/status/gpu/auth → jobs`
  展开。保留模块闭包语义是为了避免跨模块同名符号冲突（`dom.js` 与 `status.js` 都有
  `statusEl`）；转换规则未覆盖时会直接报错，不产出半坏的文件。
- **内联 CSS + 替换占位符** —— `{{APP_VERSION}}` 取自 `mediascribe/_version.py`（版本号
  单一真源），`{{STATIC_VERSION}}` 用 mtime 毫秒。
- **离线适配层** —— `file://` 下拦截 `fetch` / `WebSocket`，给出「离线预览版：界面可交互，
  提交转写需启动服务端」而不是 `Failed to fetch`。
- 边界：单文件版**只有界面**；提交转写 / WS 实时进度 / 结果取回都依赖后端 API。

### Changed (2026-10-24 全量文件分类整理：单一产品包 + 单一文档面 + 脚本分层)

仓库经过一次覆盖全部目录的结构整理，目标是消除命名误导、目录错位与文档漂移。
执行记录见 [`docs/reports/T01-文件分类整理-任务计划执行与经验沉淀报告-2026-10-24.md`](docs/reports/T01-文件分类整理-任务计划执行与经验沉淀报告-2026-10-24.md)。

- **`douyin_batch/` 包整体并入 `mediascribe/`** —— 该包名不符实：它不是抖音专用，
  而是被 30+ 处引用的共享核心库。9 个真实模块迁入 `mediascribe/`
  （`agent_output` / `browser` / `i18n` / `logger` / `platform_compat` / `report` /
  `retry` / `security` / `transcribe`），3 个转发壳（`cache` / `config` / `progress`）
  **删除**（其实现早已收编为 `mediascribe.batch_cache` / `batch_config` / `progress`）。
  ⇒ **Breaking**：`from douyin_batch.X import` 一律改为 `from mediascribe.X import`；
  `douyin_batch.cache` → `mediascribe.batch_cache`，`douyin_batch.config` →
  `mediascribe.batch_config`，`douyin_batch.progress` → `mediascribe.progress`。
- **删除根级弃用壳 `douyin_batch_v3.py`** —— 其实现早已是 `mediascribe.batch_cli`。
  ⇒ **Breaking**：命令行入口改为 `python -m mediascribe archive ...` 或
  `mediascribe-batch ...`。**注意**：JSON 输出 schema `mediascribe.agent-output/v1`
  的 `"command"` 字段值**保持 `"douyin_batch_v3"` 不变** —— 那是稳定 schema 标识而非
  脚本路径，下游 Agent 依赖它；改动需先升 schema 版本。
- **`docs_site/` → `docs/` 命名反转** —— 此前的站点源目录叫 `docs_site/`（`mkdocs.yml`
  里 `docs_dir: docs_site`），而叫 `docs/` 的目录反而只是杂项报告面，命名与直觉相反。
  现在 `docs/` 才是真正的 mkdocs 站点源，历史报告归入 `docs/reports/`，CLI 快照留在
  `docs/cli-screenshots/`。`mkdocs.yml` 的 `docs_dir` 与 `edit_uri` 同步更新。
- **`scripts/` 按职责分层** —— 53 个平铺脚本重组为
  `bench/`（基准）· `e2e/`（真实网络验证与快照）· `pipeline/`（批量转录与术语修订）·
  `tools/`（辅助工具）· `launch/`（一键启动器）· `gates/`（提交前门禁），
  外加顶层两个日常入口 `run_tests.py` / `demo_v3.py`。原 `scripts/benchmark_corpus/`
  并入 `scripts/bench/corpus/`。
- **顶层散文件归位** —— `demo_v3.py` / `run_tests.py` 进入 `scripts/`；
  `DOWNLOADERS.md` → `docs/downloaders.md`；`使用说明.md` → `docs/使用说明.md`。
  顶层仅保留社区约定文件与工具约定文件。
- **新增 `AGENTS.md`** —— 此前 `docs/index.md` 与 `.github/copilot-instructions.md`
  都指向一份**并不存在**的 `AGENTS.md`（悬空引用）。现已补齐为面向 AI Agent 的
  权威仓库指南；`.github/copilot-instructions.md` 重写为指向它的指针
  （此前写的是 Python 3.8+ / `black`+`flake8` / 38 个测试等早已过时的事实）。
- **修复：MCP `batch` 工具指向已删除的脚本** —— `mediascribe/mcp_server.py` 此前
  真的去 `subprocess` 调用 `douyin_batch_v3.py`，脚本删除后会直接失效；现改为
  `python -m mediascribe archive --json --lang en`。
- **修复：MCP `batch` 工具的 `language` 参数** —— 此前被拼成 `--language` 传给批量 CLI，
  而该 CLI 只认 `--lang`（**界面**语言）⇒ argparse 以 `unrecognized arguments` 退出 2，
  Agent 侧拿到的是非 JSON 错误而非结果。现改走批量 CLI 官方支持的
  `DOUYIN_BATCH_LANGUAGE` 环境变量（**转录**语言）。
- **修复：中文文档锚点全部失效** —— Python-Markdown 的 `toc` 默认 `slugify` 会丢弃
  非 ASCII 字符，中文标题的锚点被截成 `#1` 这类无意义值，目录锚点导航整体失效。
  `mkdocs.yml` 改用 `markdown.extensions.toc.slugify_unicode`，锚点恢复为
  `#1-经验复用梳理` 形式（与文档内既有链接写法一致）。
- **产物深度清理** —— 删除 `output/`（2275 MB / 17055 文件）、`archive/`、
  `.mypy_cache/`（120 MB）、`.ruff_cache/`、`.pytest_cache/`、`web-workspace/`
  与全部 `__pycache__`，合计释放约 2.4 GB。清理前已把**不可再生**的转录成果
  （124 份转录稿 + 75 份人工审校稿 + 运行元数据）备份到仓库外的
  `D:\MediaScribe-archive-2026-10-24\`。

### Added (2026-10-24 选型落地：权重预置 + 简繁统一 + whisper.cpp 第三条腿 + 运行时守卫 + 前端拆分)

- **前端拆成原生 ES modules**（零构建、零依赖，不换框架）——
  `index.html` 42,854 B → **7,329 B（−83%）**，冷启动 467.5 ms → **84.7 ms（−82%**，
  拆分后 CSS/JS 可并行下载且可分别缓存）。拆为 `styles.css` + `app.js` +
  `modules/{dom,theme,status,gpu,auth,jobs}.js`，知识库页同样拆出 `wiki.css`/`wiki.js`。
  * **gzip 总量 +17.4%（+3 KB）** —— gzip 靠字典共享压缩单文件，拆多文件后各文件独立
    压缩、字典无法共享 ⇒ gzip 必然上升。保留 6 个语义清晰的模块而非合并成 2–3 个
    大文件；真要压缩传输体积，正解是服务端预压缩而非放弃拆分。
  * **移除 Google Fonts 外链** ⇒ 页面**零外部网络请求**（此前每次打开都要连
    fonts.googleapis.com，与「完全离线」硬约束冲突）。
- **运行时资源守卫** `mediascribe/runtime_guard.py` —— 把基准侧的硬件自适应与
  过载护栏**下沉到产品运行时**（此前护栏只护基准脚本，转录产品本身无保护）：
  * 转录前按资源**自动降档**（`large-v3 → medium → small → base → tiny`）或换引擎
  * 四条规则各有实测出处：Apple Silicon（faster-whisper 无 Metal 后端）⇒ 换
    whisper-cpp；CPU + 可用内存 < 4 GB ⇒ 换 whisper-cpp（实测最省内存 291 MiB）；
    显存/内存不足 ⇒ 按阶梯降模型档
  * 转录中后台线程每 2s 采样内存/显存/温度，越紧急线 ⇒ **通知 + 请求取消**
  * 转录结束后若确曾紧急过载 ⇒ 抛 `ResourcePressureError`，**不交付可能不完整的结果**
  * 降档说明随转写稿 `metadata.resource_notices` 落盘 ⇒ 用户能看到「为什么换了小模型」
- **修复**：降档只改局部变量导致 `settings.model` 与实际用量不符（metadata 会记错）；
  降档阶梯在大模型族上死循环（`large-v3 → large-v2 → large-v2…`）；
  `mediascribe/transcribers/__init__.py` 漏导出 `WhisperCppTranscriber`（整个包 ImportError）。
- **估算表对齐**：产品侧用实测值、基准侧仍是早期估算（7 处，如 faster-whisper/small
  显存 614 vs 800 MiB）⇒ 新增 `tests/test_estimate_tables_consistency.py` 锁住两表一致。

- **模型权重清单** `mediascribe/model_weights.py` —— 把「某 engine×model 需要什么权重、
  在哪、怎么预置」变成可查询的数据。三个引擎的目录约定各不相同（faster-whisper /
  whisperx 走 HuggingFace 缓存、openai-whisper 走 `~/.cache/whisper/*.pt`、
  whisper.cpp 走 GGML 单文件），此前缺权重只能在跑到一半时炸。
- **权重加载失败改为带指引的清晰报错** —— 此前 HuggingFace 的
  `LocalEntryNotFoundError` / openai-whisper 的 `Model xxx not found` 对用户
  没有可操作性；现在附带**具体预置路径与命令**。
- **`mediascribe doctor` 新增「权重预置」自检段** —— 按引擎列出各档位就绪情况
  与缺失明细，避免「跑到一半才发现权重没预置」。
- **中文简繁统一** `mediascribe/zh_normalize.py` —— 双层防御：
  ① **源头**（跨平台有效，主力）：openai-whisper / whisperx / whisper.cpp
  在中文场景注入简体 `initial_prompt`（此前只有 faster-whisper 有，导致这
  三个引擎输出**繁体**）；② **兜底**（Windows 有效）：输出层用
  `LCMapStringW` 转简体，**零第三方依赖**。
  实测依据：同一段音频字级 CER，openai-whisper **33.61% → 0.41%**、
  whisper.cpp **81.56% → 1.19%**（差的是字形不是识别能力）。
- **繁简开关** —— `Settings.zh_simplify`（默认**开**）/ CLI `--no-zh-simplify` /
  环境变量 `MEDIASCRIBE_ZH_SIMPLIFY`；优先级：显式参数 > env > 默认。
  关闭只关掉输出层兜底，引擎侧的简体引导仍生效。
- **whisper.cpp 第三条引擎** `mediascribe/transcribers/whisper_cpp.py` ——
  资源受限场景（无 GPU / 4 GB 内存老机器）的回退腿。实测依据（CPU 档峰值内存）：
  whisper.cpp tiny **291 MiB** vs faster-whisper small 952 MiB vs
  faster-whisper large-v3 3613 MiB。内置简体 prompt 注入、16 kHz 转码、
  缺二进制/缺权重时给出预置指引。`--engine whisper-cpp` 可显式选用。
- **whisper.cpp 引擎层 VAD 默认关闭**（2026-10-24 实测推翻原先「必须配 VAD」的
  假设）：干净中文 vad=False **7.38%** vs vad=True 19.67%（VAD 反而恶化）；
  重度噪声 94.67% → **81.97%**（改善 12.7 pt）。VAD 只在噪声场景有效，
  而管线层已有 `webrtcvad` + 分块逻辑 ⇒ 引擎层重复处理会切掉有效语音。
- **whisper.cpp 运行时 DLL 预检** —— 官方包是动态链接的，`whisper-cli.exe`
  依赖同目录 15 个 DLL；只拷 exe 会以 `STATUS_DLL_NOT_FOUND`（0xC0000135）
  **静默失败**。新增 `missing_runtime_dlls()` 预检与专门的错误分支。
- **内存压力下的回退链自适应** —— 可用内存低于 4 GB 且设备为 CPU/auto 时，
  `whisper-cpp` 自动排到 `faster-whisper` 之前（它最省内存）。
- **新增 56 项测试**（`tests/test_zh_normalize.py` / `tests/test_model_weights.py` /
  `tests/test_whisper_cpp_transcriber.py`），覆盖三层防线与二进制探测的
  误命中防护。
- **引擎名单跨入口一致性测试** `tests/test_engine_consistency.py` —— 引擎名单在
  仓库里有四处独立维护（`config.py` / CLI choices / web 层 pydantic 正则 /
  factory 回退链）。新增 `whisper-cpp` 时漏改了 **web 层正则** ⇒ Web UI 选该
  引擎会被 422 拒绝。新增测试断言四处**完全一致**，任何一处漏改 CI 立刻红
  （已做反向验证：故意改回旧正则 ⇒ 3 failed）。

### Added (2026-10-23 技术选型对比与基准基建)
- **技术选型对比报告**: `docs/tech-selection-report-2026-10.md` —— 前端框架 / 工作台壳 / 推理引擎
  三层候选的完整对比（定位与生态 / 核心能力覆盖 / 性能与成本 / 上手成本与踩坑 / 维护活跃度），
  含本机实测数据与跨硬件适用矩阵。
- **真实语音基准语料生成器**: `scripts/benchmark_corpus/gen_corpus_real.py` + `sapi_tts.ps1` —— 用 Windows SAPI
  **完全离线**合成 10 条真实语音语料（中文 3 音色 + 术语密集 + 口语 + 英文 + 中英混说 + 轻噪 + 两档重度退化，
  合计 10.2 分钟）。参考文本天然已知 ⇒ CER 可精确计算。`anoisesrc` 固定种子 ⇒ 两次生成 SHA-256 一致
  （修复前同参数重建错误率可在 23.8% ↔ 45.9% 间漂移）。
- **L3 推理引擎基准**: `scripts/bench/asr/bench_asr.py` —— 硬件自适应（显存/内存不足自动降档或跳过）、
  资源护栏（GPU 温度硬阈值、CPU 持续占用、系统内存紧急线/就绪线分离、磁盘余量、单轮熔断）、
  进度提示（轮内心跳 + `progress.json` + ETA）、**断点续跑**（安全中止不丢已完成轮次）。
- **L1 前端框架基准**: `scripts/bench/frontend/scaffold_candidates.py` + `bench_frontend.py` —— 模板化生成
  vanilla / React / Vue / Svelte / Solid 五个候选（统一 DOM 结构与埋点协议，保证口径一致），实测
  构建时长 / 产物体积 / 依赖规模 / 冷启动 / 首屏渲染 / 筛选响应 / 滚动帧率。
- **L2 工作台壳基准**: `scripts/bench/shell/` —— B1 FastAPI+浏览器 / B2 预构建静态站点 / B3 Tauri /
  B4 Electron / B5 浏览器 WASM 的冷启动、驻留内存、分发体积、离线自包含、跨平台五维实测。
- **中文繁简归一化**: `scripts/bench/asr/zh_normalize.py` —— 用 Windows `LCMapStringW` 做繁→简转换，零第三方依赖。
  **不归一化会把字形差异误判成识别错误**：实测 openai-whisper 与 whisper.cpp 中文输出繁体，
  同一段音频的字级 CER 虚高到 33.61% / 81.56%，归一化后为 0.41% / 1.19%。
- **基准汇总与重算工具**: `scripts/bench/asr/summarize_asr.py`（严格区分成功轮与未完成轮，
  避免失败轮的 RTF 污染统计）、`scripts/bench/asr/recompute_cer.py`（口径修正后**无需重跑推理**即可重算 CER）。

### Fixed (2026-10-24 端到端集成验证暴露)
- **前端拆分后静态资源全部 404 —— 生产环境下页面完全不可用**：FastAPI 侧只有 `/`
  与 `/wiki` 两个路由（读 HTML 文本返回），**没有任何静态文件服务**，而拆分后的页面
  引用了 `styles.css` / `app.js` / `modules/*.js`。已挂载 `StaticFiles` 到 `/static`
  并把页面引用改为绝对路径。**既有测试一个都没红**（只读 HTML 文本、不验证资源可达）⇒
  新增 `tests/test_web_app_static.py`（5 项），其中一条**通用断言**：解析页面里所有
  本地 `href`/`src` 逐个断言 200，以后再加文件漏路由会立刻被抓住。
- **Web UI 引擎下拉漏了 `whisper-cpp`**（CLI choices 改了、页面 `<select>` 没改）——
  现已被 `test_engine_selector_lists_all_engines` 覆盖。
- **转写稿的「转录引擎」字段错显模型名**（此前显示 `small`，而 metadata 里
  `engine` 是 `faster-whisper`）⇒ 引擎名与模型名分两行显示。

- **降档通知在提交响应里即时返回** —— `POST /api/jobs` 现在带回 `notices` 字段，
  前端状态条**当场**显示「这次为什么用了小模型」（此前只能在转写完的 metadata 里看到）。
  降档决策发生在 pipeline 构建时（= 提交那一刻），选提交响应而不是 WebSocket 事件：
  此时 WS 还没连上，而提交响应是同步返回的。
- **新增 7 项测试**（`tests/test_web_app_static.py`）—— 静态资源可达性（含「解析页面里
  所有本地 href/src 逐个断言 200」的通用防回归）、页面无外部请求、引擎下拉齐全、
  提交响应带回 notices。
- **修两个既有标签 bug**：转写稿「转录引擎」字段错显模型名（此前显示 `small`，
  而 metadata 里 `engine` 是 `faster-whisper`）⇒ 引擎名与模型名分两行显示。

### Fixed (2026-10-23 Web UI 启动路径)
- **`python -m mediascribe.web.app` 此前必然失败**（`NameError: name '_register_health_routes' is not defined`）——
  `__main__` 块位于文件中部，早于路由注册函数的定义。已移至文件末尾。
- **`uvicorn mediascribe.web.app:app` 此前必然失败**（`Attribute "app" not found in module`）—— 模块没有模块级
  `app` 对象，而模块 docstring 与 `scripts/one_click_up.py` 一直宣称该启动方式。已补 `app = create_app()`
  （仅导入时创建，避免 `python -m` 路径重复建实例）。
- **`docs_site/benchmarking.md` 文档漂移** —— 该页描述了**不存在的** CLI（`--real` / `--audio-path` / `--engine` /
  `--audio-seconds` / `--iterations`）与**不存在的**输出目录 `./bench-results/`，而真引擎路径长期是
  `NotImplementedError` ⇒ 文档里的命令根本不可能跑通。已按实际实现重写该页（四套基准的真实参数、口径、
  硬件自适应与护栏说明、复现性与局限）。
- **新增启动路径回归测试** `tests/test_web_app_startup_paths.py`（6 项）—— 断言模块级 `app` 存在且能返回
  `/api/health` 200、断言 `__main__` 块位于所有路由注册函数之后（直接锁住上面两个缺陷的根因）、
  断言 `python -m mediascribe.web.app` 可执行。自动进入 CI `unit` 门禁。

### Added (2026-10-08 存疑复核闭环)
- **`verify` 子命令**: `python -m mediascribe verify <转录稿.md>` — 第三采样验证。
  复用或按 metadata 来源 URL 重新下载音频 → 无术语注入 + 多档温度独立重采样
  (与主稿形成解码级独立样本) → 写时间戳采样稿 `third-sample-*.md` → 对
  `.crosscheck.json` 实词分歧逐条给出近似对齐证据(同主稿/同对照/第三读/未定位),
  写 `<转录稿>.verify.md` 供人工裁决。配合"审校完再清理媒体"的工作流使用。
- 清理中间媒体前打印存疑复核提示(stage 链与公众号兜底路径两处调用点)。

### Changed
- **faster-whisper 模型加载改本地优先**: 已缓存模型零网络请求, 消除受限
  环境每次转录的 SSL 告警噪音; 仅本地缓存未命中才联网下载(新模型照常可下)。
  `FasterWhisperTranscriber.transcribe` 支持 `temperature` 透传(verify 用)。

### Fixed (2026-10-03 全面审查)
- **发布链修复**: release.yml lint 补装 bandit(dev extra 缺失导致 tag 一推必挂)、
  `--skip` 误用改 `--exclude`、Docker 多架构改单 job manifest list(原 amd64/arm64
  短阵互覆盖)、全 job 补 timeout、test job 收敛为单 sanity 组合; bandit.yml 收敛单
  ubuntu(原三平台上传同名 artifact 必冲突); codecov v4→v5 并留档 coverage.xml
- **安全默认**: docker-compose 端口只绑 127.0.0.1; 无 token 模式拒绝公网来源
  (loopback/私网/docker 网关放行); WS token 首选子协议不再走 URL query;
  短链重定向最终目标 SSRF 复查; .gitignore 补 config.json; wiki.html DOM XSS 两处
- **测试体系**: 测试搬家顶层 `tests/`(原 douyin_batch/tests 错位且打进发行包)+
  新增 conftest.py; 删除零生产引用的 observability.py 幽灵特性(模块+otel extra+
  文档+26 个测试); coverage_gaps 1/2/3 按模块拆回; 脚本伪装测试改真测试;
  e2e 挂 marker 使 CI `-m` 过滤首次生效; Path.cwd() 工作区改 tempfile;
  doctor.py 首个专属测试(15 用例); mcp_server/faster_whisper 移出 coverage omit
- **架构**: 根级 douyin_batch_v3.py 收编为 `mediascribe.batch_cli`(entry point/
  py-modules/动态加载三层耦合一次解决, 根级留弃用壳); create_app(817 行)拆 9 个
  route registrar
- **工具链**: requires-python 下限 3.8→3.11(3.8-3.10 已 EOL), mypy/ruff target/
  CI 矩阵(3.11-3.13)对齐; openai-whisper 下限抬 20250625(修 torch>=2.6
  weights_only 必崩组合); dev extra 钉 ruff 0.16.x 并补 bandit; run_tests.py 改
  pytest 垫片; 新增 scripts/pre_push_gate.py 门禁 + justfile/Makefile gate/push
- **数据治理**: 术语库路径支持 `MEDIASCRIBE_LEARNED_TERMS` 覆盖; `learn clear`
  清空前自动留时间戳备份; vault 落点支持 `MEDIASCRIBE_VAULT_DIR` 覆盖;
  archive/ 从 git 追踪中移除(规则与现状一致化); CLI 快照重建

### Added
- **`--cleanup-media`: auto-delete intermediates after successful
  transcription** (v3.4.3). New `CleanupStage` at the tail of the stage
  chain removes the task's downloaded video (`downloads/`) and extracted
  audio (`audio/`) once the transcript + metadata are written —
  transcripts, metadata and wiki outputs are never touched. Safety
  rails: only files inside the managed `audio_dir` / `downloads_dir`
  are deleted (user-supplied local media and the persistent download
  cache are exempt), failures to delete are logged and ignored, and
  nothing is deleted when any earlier stage fails. With
  `--cross-check`, cleanup is deferred until the second-pass model has
  re-transcribed the same audio. Also applied to the WeChat-MP video
  article path (which bypasses the stage chain). Enable via CLI flag,
  `--config` JSON key `cleanup_media`, or `MEDIASCRIBE_CLEANUP_MEDIA=1`.
- Fixed `TestPackageMain.test_no_args` reading unmocked `sys.argv` —
  it passed in CI only by accident (pytest's path argument was treated
  as an unknown mediascribe command) and crashed with `SystemExit(2)`
  under `python -m pytest -q`.

### Changed
- **Project renamed: Video2Text → MediaScribe** (breaking). The Python
  package, distribution name, CLI commands, and environment-variable prefix
  all change:
  - package `video2text` → `mediascribe` (all imports)
  - PyPI/distribution `video2text` → `mediascribe`
  - CLI `video2text` → `mediascribe`; `python -m video2text` →
    `python -m mediascribe`
  - CLI `video2text-batch` → `mediascribe-batch`
  - env prefix `VIDEO2TEXT_*` → `MEDIASCRIBE_*` (e.g.
    `MEDIASCRIBE_API_TOKEN`, `MEDIASCRIBE_LLM_ENABLED`,
    `MEDIASCRIBE_DOWNLOAD_CACHE`)
  - web UI, extension, docs, and MCP server copy updated accordingly
  - `douyin_batch` package and `douyin_batch_v3` module keep their names;
    only their `video2text` imports were updated
  - the `archive/` legacy folder and historical CHANGELOG entries are
    intentionally left untouched

## [3.3.0] - 2026-09-10

### Fixed (engineering / packaging)

- **Docker image build** (P1-5): `Dockerfile` now copies `douyin_batch/` and
  `douyin_batch_v3.py` into the build context so `pip install .` no longer
  fails with "package directory 'douyin_batch' does not exist".
- **Wheel contents & entry points** (P1-6): `pyproject.toml` now packages
  `video2text.web`, `video2text.plugins` and the `examples.plugins` entry
  point targets, and declares `douyin_batch_v3` as a top-level py-module, so
  `video2text-batch` and the vimeo/tcn plugin entry points resolve after
  install. Verified with `pip wheel` + unpack inspection.
- **`openai` dependency declared** (P1-3): new `llm` extra
  (`openai>=1.0.0`) for `video2text.llm_post_process`; included in `all`.
- **`otel` extra** (P2-12): `opentelemetry-sdk>=1.20` for the optional
  OpenTelemetry export backend in `video2text.observability`.
- **`make lint` / `make format`** (P1-4): now run `ruff check .` /
  `ruff format` instead of the uninstallable flake8+black combo; dead
  `BLACK`/`ISORT`/`FLAKE8` variables removed; `test-i18n`/`test-cross`/
  `test-imports`/`test-syntax` added to `.PHONY`.
- **LLM post-process defaults are safe** (P1-5): `enabled` now defaults to
  `False` and must be turned on explicitly via `VIDEO2TEXT_LLM_ENABLED`;
  `api_base` no longer silently defaults to a vendor endpoint — enabling
  without configuring it raises a clear error at `Settings` construction;
  truncation keeps the head (comment corrected) and `finish_reason=length`
  now logs a truncation warning.
- **`batch` subcommand exit code** (P1-6): partial failures now exit `1`
  instead of `0`; `--device` accepts `auto`.
- **`Settings` validation** (P1-7): engine/model validated at construction
  (`ValueError` on unknown values, aligned with `transcribers/factory.py`
  and the web layer); missing cookie files log a warning instead of failing
  silently; `VIDEO2TEXT_WORKSPACE` env is honoured (explicit argument still
  wins, env beats the `./output` default).
- **Docker Compose** (P1-7): image pinned to the local build
  (`video2text:local`) instead of a drifting `web-3.1.0` tag; `/etc/localtime`
  mount commented out with a Windows-compatibility note; comments match
  actual behaviour.
- **Packaging metadata** (P1-1): `license = {text = "MIT"}` (setuptools>=61
  compatible), classifier downgraded to *Development Status :: 4 - Beta*
  to match the `3.2.0a` pre-release.
- **requirements single-sourcing** (P2-1): `requirements.txt` is now a thin
  shim (`-e .[web,ocr,faster-whisper,mcp]`) over `pyproject.toml`;
  `requirements-dev.txt` is `-e .[dev]`.
- **CI drift** (P2-3): `bandit.yml` also triggers on `develop`;
  `release.yml` test job installs the same extras as `test.yml`;
  duplicate non-strict `build-docs` job removed from `test.yml`
  (`docs.yml` with `--strict` is authoritative); dependabot comment now
  describes the real PR matrix.
- **ruff config** (P2-4): removed `ignore` entries for rules that were never
  selected (`B008`/`UP045`/`SIM`/`B`); `E501` comment now reflects reality.
- **`.gitignore`** (P2-5): `output/` → `output/*` + `!output/.gitkeep` so
  the negation actually works; added `.ruff_cache/`, `test-ws-*/`,
  `pytest-cache-files-*/`.
- **Tracked residue removed** (P2-6/P2-7): `test_chunked_src.md`,
  `docs/lint-report.json`, `docs/lint-report.md` and the orphaned
  `douyin_batch/tests/test_skill_frontmatter.py` (asserted a `.trae/`
  directory that is not in the repo) deleted.
- **profile CLI docs** (P2-10): documented command corrected to
  `python -m video2text.profile_cli`; the CLI exits `2` with a clean
  message on missing file / bad `--since`; README no longer advertises a
  CSV output format.

### Fixed (transcribers / pipeline core)

- **`ChunkedTranscriber` interface** (P0): `transcribe` now matches the
  `Transcriber` base contract; the inner engine is called with keyword
  arguments, so chunked transcription works with every built-in engine
  instead of raising `TypeError`. Chunk windows no longer double-apply the
  overlap (each chunk boundary was re-transcribed twice), merged segments
  drop timeline-overlapping duplicates, and the temporary chunk directory is
  cleaned up in `finally` (previously leaked ~19 MB per 10 min of audio).
- **WhisperX model caching** (P1): the main model and alignment models are
  now cached module-level (double-checked lock) instead of being reloaded on
  every transcription; `clear_model_cache()` releases VRAM.
- **faster-whisper model cache LRU** (P1): `_MODEL_CACHE` is now an
  `OrderedDict` LRU capped at 2 models, so switching model/device combos no
  longer accumulates VRAM until CUDA OOM.
- **`torch.load` monkeypatch scoped** (P2): `video2text/transcribers/
  whisper.py` no longer replaces `torch.load` process-wide at import time;
  the compatibility patch now applies only around `whisper.load_model`.
- **Device resolution in the factory** (P1): `Pipeline._create_transcriber`
  now calls `resolve_device()`, so `"auto"` no longer reaches the engines
  (faster-whisper gets `int8_float16` on CUDA; WhisperX alignment no longer
  crashes on `"auto"`).
- **Process-wide GPU semaphore** (P1): GPU concurrency is now shared across
  all `AsyncPipeline` batches, so two concurrent batch jobs can no longer
  double the GPU task count into a CUDA OOM.
- **Cancellation semantics** (P1): cancelling in-flight tasks converts
  `CancelledError` into `_FailedResult` instead of breaking the `gather`
  contract; the cancel event is cleared per `run_batch` and is checked
  cooperatively between stages; `Pipeline.transcribe` accepts
  `cancel_event`.
- **Download cache wiring** (P2): `PersistentDownloadCache` is now actually
  used by `DownloadStage` when `VIDEO2TEXT_DOWNLOAD_CACHE=1` (hit = reuse
  cached file, miss = download + store; off by default).
- **Cache hardening** (P2): cache index is persisted on write/close instead
  of on every hit; file copies are atomic (tmp + `os.replace`), so
  half-written files can no longer be cached as valid; `PersistentChunkCache`
  gained TTL pruning and an LRU entry cap.
- **Audio utilities** (P2): `extract_audio` output names carry a
  path+size+mtime fingerprint so concurrent same-title jobs no longer
  overwrite each other's wav (failures clean up partial files); VAD now
  streams the PCM instead of loading the whole audio into memory.
- **Profile timing isolation** (P2): step timings are contextvars-scoped
  per run, so concurrent `AsyncPipeline` runs no longer clobber each
  other's `STEP_TIMES`.
- **Misc** (P2): download fallback chain deduplicated into
  `_smart_pick_downloader`; `engine_name` declared on `PipelineContext`;
  critical stage-chain `assert`s became real exceptions; `VIDEO2TEXT_CUSTOM_TERMS`
  JSON errors are logged instead of swallowed; active span tracking is
  contextvars-based (concurrency-safe).

### Fixed (web / MCP security)

- **WebSocket authentication** (P1): `/ws/progress/{job_id}` now validates
  the shared token (query `?token=` or `Sec-WebSocket-Protocol`) before
  accept, closing the unauthenticated read/cancel bypass; the built-in UI
  gained an API-token field (persisted in `localStorage`) and sends Bearer
  headers + the WS token.
- **SSRF hardening** (P1): submitted URLs must resolve to public addresses
  (private/loopback/link-local/reserved are rejected fail-closed; userinfo
  tricks rejected); `VIDEO2TEXT_ALLOWED_HOSTS` opt-out for local testing.
- **Local-file exfiltration closed** (P1): the web API no longer accepts
  arbitrary local media paths — only URLs, or paths inside the workspace.
- **MCP HTTP transport** (P1): optional `VIDEO2TEXT_MCP_TOKEN` (401),
  Host allow-list (403, anti DNS-rebinding), 1 MiB body cap (413), malformed
  JSON-RPC no longer kills the stdio loop, `max_videos` clamped to schema.
- **Rate limiter** (P2): expired buckets are swept globally (60 s interval);
  docstring matches behaviour and documents the `--proxy-headers` caveat.
- **Hardening sweep** (P2): `file://` CORS origin removed; JSON endpoints
  reject non-`application/json` content types (415); batch thread no longer
  swallows `BaseException`; internal exception details are logged, not
  echoed; duplicate URLs are deduplicated on submit; WS sessions get max
  duration + idle timeouts via a per-job event bridge (no more 0.5 s
  thread-pool polling per connection); progress events queue is bounded;
  generated INSTALL.md uses `VIDEO2TEXT_PUBLIC_BASE_URL` or a sanitised
  request origin.

### Fixed (downloaders / douyin_batch)

- **Douyin downloader** (P0): missing Playwright now returns `(None, None)`
  and falls back to yt-dlp instead of crashing on tuple unpacking.
- **`--config` entry point** (P0): `BatchConfig` gained `user_url`, so
  config-file-driven runs work as documented.
- **Config contract** (P1): env prefix corrected to `DOUYIN_BATCH_*` (old
  `DOYIN_BATCH_*` still read with a deprecation warning); `TranscriberPool`
  now honours `whisper_model`/`language` from the config; `workers > 1`
  actually parallelises with a thread pool; `scroll_pause`/
  `max_scroll_rounds` wired into the browser session.
- **Resume semantics** (P1): failed videos are no longer marked processed
  (they retry on the next run) and their audio files survive cleanup.
- **WeChat MP extractor** (P1): `js_content` extraction balances nested
  `<div>`s instead of truncating at the first inner `</div>` (silently
  dropping the rest of the article).
- **Downloader convergence** (P1): new shared `downloaders/_http_download.py`
  (retrying session, `.part` temp file, failure cleanup) used by douyin /
  xiaohongshu / wechat_mp; xiaohongshu image notes fail with a clear
  message instead of feeding a `.jpg` to ffmpeg.
- **Hygiene** (P2): PaddleOCR reader cached (was re-initialised per image);
  uuid-suffixed filenames prevent same-second collisions; library prints →
  logger (stdout stays clean under `--json`); batch cache writes are atomic
  with corruption quarantine; version sourced from
  `douyin_batch.__version__`; yt-dlp fetches metadata once with throttled
  progress hooks; ffmpeg merge failures log stderr; `BrowserManager` no
  longer launches a browser on cache-hit shutdown; deprecated
  `locale.getdefaultlocale()` replaced; report filenames ASCII-fied for the
  i18n CLI; short-link host matching uses parsed hostname (closing the
  `http://b23.tv@127.0.0.1/` bypass).


## [3.2.0a] - 2026-06-06

> **Alpha milestone** — Tier 2 polish for v3.1.0.  Four new subsystems land
> behind a single `--mode` switch and stay disabled-by-default at the
> CLI / Web level.  No breaking API changes.  Test count jumps from
> **473 → 612 passed (+139 new)**, 9 skipped, 0 failed, ruff clean.

### Added
- **VAD-based long-video chunking**
  ([audio_utils.py](file:///d:/1/video2text/video2text/audio_utils.py),
  [transcribers/chunked.py](file:///d:/1/video2text/video2text/transcribers/chunked.py)):
  chunk splits are now driven by `webrtcvad` voice-activity detection
  when the optional dependency is installed.  Audio is scanned in
  20 ms frames at mode-3 aggressiveness; chunks are sealed on the
  first non-speech window of at least 700 ms, guaranteeing a real
  breath or sentence boundary instead of a hard 30 s cut.  A 5 s
  overlap is retained at the chunk edge so ASR boundary effects
  do not eat the first/last word.  When `webrtcvad` is not
  importable the code falls back to the legacy fixed-duration
  splitter, so existing callers are unaffected.  20 new tests in
  [test_vad_chunking.py](file:///d:/1/video2text/douyin_batch/tests/test_vad_chunking.py)
  cover the activation flag, silence/speech mix, fallback path,
  overlap stitching and PCM16 round-trip.
- **Cross-run persistent cache**
  ([cache.py](file:///d:/1/video2text/video2text/cache.py)):
  downloads and transcriptions are now memoised to disk across
  processes, not just within a single Python run.  Layout follows
  the XDG Base Directory spec — the cache lives at
  ``$XDG_CACHE_HOME/video2text`` (or ``~/.cache/video2text`` on
  Linux, ``%LOCALAPPDATA%\video2text\cache`` on Windows, and
  ``~/Library/Caches/video2text`` on macOS) and is overridable via
  the ``VIDEO2TEXT_CACHE_DIR`` environment variable.  Each entry is
  a SHA-256 of `(platform, url, params)` + a per-key JSON payload,
  with a single index file per category (``downloads.json`` /
  ``transcripts.json``) for O(1) lookup.  An LRU cap (default 1 GiB
  / 4096 entries) and a 30-day TTL prune are enforced on every
  write; ``Cache.prune()`` is exposed so cron / CI can garbage
  collect manually.  Stale or corrupt index files are detected via
  a monotonic schema version and silently rebuilt.  21 new tests
  in
  [test_persistent_cache.py](file:///d:/1/video2text/douyin_batch/tests/test_persistent_cache.py)
  cover XDG fallback, env override, LRU eviction at zero cap,
  TTL=0 semantics, OSError on ``_save_index``, and 4 KB
  payload round-trip.
- **`profile` CLI sub-command**
  ([__main__.py](file:///d:/1/video2text/video2text/__main__.py),
  [profile_cli.py](file:///d:/1/video2text/video2text/profile_cli.py)):
  ``python -m video2text profile <run.jsonl>`` aggregates pipeline
  profiling JSONL output into a Markdown report, a JSON summary,
  or a CSV — the 3-axis breakdown (stage / event / percentiles)
  is identical to the Web UI.  The dispatcher preserves the v3.1.0
  ``transcribe`` / ``batch`` entry points verbatim, so existing
  scripts and CI pipelines are not touched.  New flags:
  ``--top N`` (default 10) limits the slowest-events table,
  ``--since ISO`` filters records older than the given
  timestamp, ``--format {md,json,csv}`` switches the output
  format, and ``--tz-aware`` normalises naive timestamps to
  UTC before comparison.  26 new tests in
  [test_profile_cli.py](file:///d:/1/video2text/douyin_batch/tests/test_profile_cli.py)
  cover parse-error fallback, percentile math, CSV escaping,
  timezone-aware filtering, top-N clamping to 0, and exit codes
  on empty input.
- **WebSocket real-time job progress**
  ([progress.py](file:///d:/1/video2text/video2text/progress.py),
  [web/app.py](file:///d:/1/video2text/video2text/web/app.py)):
  long-running ``/api/transcribe`` and ``/api/batch`` jobs now
  publish a 3-bar progress event (download / transcribe /
  assemble) that the dashboard can stream without polling.  The
  new ``ProgressRegistry`` lives on ``app.state.jobs`` and exposes
  ``create(url)``, ``get(job_id)``, ``cancel(job_id)``,
  ``list()`` and ``purge(max_age_seconds)``.  Three new endpoints
  wire it into FastAPI:
  - ``GET  /api/jobs/{job_id}`` — JSON snapshot of stage
    percentages, current event name, started / updated / finished
    timestamps and final result.
  - ``POST /api/jobs/{job_id}/cancel`` — cooperative
    cancellation; the worker checks the flag between chunk
    boundaries so partial chunks are not orphaned on disk.
    Unknown ``job_id`` returns ``404`` (not 200 + ``cancelled=False``).
  - ``WS   /ws/progress/{job_id}`` — bi-directional stream that
    pushes a snapshot on connect and then a ``progress`` /
    ``completed`` / ``cancelled`` / ``error`` event for every
    stage update.  Uses ``asyncio.to_thread`` to bridge the
    background thread that owns the queue, so the event loop
    never blocks on a ``queue.get()``.  Unknown ``job_id``
    triggers a 4404 close frame with an ``error`` payload so the
    client can show a clean message.
  19 new tests across
  [test_job_progress.py](file:///d:/1/video2text/douyin_batch/tests/test_job_progress.py)
  and
  [test_web_app_security.py](file:///d:/1/video2text/douyin_batch/tests/test_web_app_security.py)
  cover registry lifecycle, cancellation between stages, WS
  snapshot replay, 4404 on unknown id, and the 404 / 200
  semantics of the REST helpers.

### Changed
- **`__main__.py` refactored into a dispatch shim**
  ([__main__.py](file:///d:/1/video2text/video2text/__main__.py)):
  the entry point now parses the first positional argument and
  routes to ``_run_legacy`` (``transcribe`` / ``batch`` /
  ``-h``) or to the new ``profile`` sub-command.  All v3.1.0
  CLI flags and exit codes are preserved bit-for-bit, so
  ``python -m video2text --help`` still exits 0 with the same
  argparse output, and ``python -m video2text batch ...``
  behaves exactly as before.

### Test infrastructure
- **Coverage gap round 3**
  ([test_coverage_gaps_3.py](file:///d:/1/video2text/douyin_batch/tests/test_coverage_gaps_3.py)):
  16 new tests target the 0%-or-low-% branches surfaced by the
  v3.2.0a code review — corrupted index recovery, TTL = 0
  short-circuit, LRU eviction with cap = 0, OSError handling in
  ``_save_index``, ``_parse_iso`` invalid input fallback,
  unparseable-timestamp passthrough, ``--top 0`` rendering,
  timezone-aware vs naive filtering, WebSocket unknown-id close,
  WebSocket snapshot replay, REST 404 / 200 semantics and
  ``purge`` cut-off behaviour.  Result: ``cache.py`` 92% → 94%,
  ``profile_cli.py`` 93% → 97%, ``web/app.py`` 71% → 82%,
  ``progress.py`` 100%.

### Quality gate
```
612 passed, 9 skipped in 213.52s
ruff check .                            # 0 errors
coverage report -m (video2text scope)   # 75% overall, all v3.2.0a modules ≥ 89%
```

## [3.1.0] - 2026-06-04

### Added
- **Browser Side Panel support**
  ([manifest.json](file:///d:/1/video2text/extension/manifest.json),
  [background.js](file:///d:/1/video2text/extension/background.js),
  [popup.html](file:///d:/1/video2text/extension/popup.html),
  [popup.js](file:///d:/1/video2text/extension/popup.js),
  [popup.css](file:///d:/1/video2text/extension/popup.css)):
  the Chrome / Edge extension now opens the same UI in the browser
  Side Panel (``chrome.sidePanel``) as in the toolbar popup.
  ``manifest.json`` declares ``sidePanel`` permission and
  ``side_panel.default_path = "popup.html"`` so users can pin the
  panel to the right edge of the window for hands-free transcript
  reading while the page scrolls on the left.
  - The popup page detects which surface it is on via
    ``chrome.sidePanel`` presence and applies a wider layout for
    the panel (``body.is-side-panel``).
  - The popup shows a new "Open in side panel" button that calls
    ``chrome.sidePanel.open`` locally, with a message-bus fallback
    through the background worker for older Chromium builds.
  - ``background.js`` configures the side panel on install, falls
    back to opening the panel when the toolbar icon is clicked with
    no popup, and exposes a ``v2t/openSidePanel`` message handler.
  - Styles are extracted into ``popup.css`` with light/dark theme
    variables and ``prefers-color-scheme`` support so both surfaces
    look identical.
  - ``minimum_chrome_version`` bumped to ``114`` (the Side Panel
    API shipping version).
  - 31 new tests in
    [test_extension_side_panel.py](file:///d:/1/video2text/douyin_batch/tests/test_extension_side_panel.py)
    cover the manifest contract, HTML element wiring, JS function
    presence, and ``node --check`` syntax validation.
- **One-click extension ZIP download**
  ([extension_builder.py](file:///d:/1/video2text/video2text/web/extension_builder.py),
  [web/app.py](file:///d:/1/video2text/video2text/web/app.py),
  [static/index.html](file:///d:/1/video2text/video2text/web/static/index.html)):
  the Web UI now lets end users grab a self-contained ZIP of the
  ``extension/`` tree without leaving the browser, so they can
  ``chrome://extensions`` → "Load unpacked" on Chrome / Edge
  without cloning the repo.  Three new endpoints:
  - ``GET /extension`` — dark-theme landing page that mirrors the
    README install steps and links to the ZIP / install doc.
  - ``GET /api/extension/download`` — streams the ZIP with
    ``Content-Disposition: attachment``,
    ``X-Content-Type-Options: nosniff`` and
    ``Cache-Control: no-store`` so a misclick never caches a stale
    build.
  - ``GET /api/extension/install.md`` — same steps as
    Markdown, served as ``text/markdown`` when ``?raw=1`` is set
    and as a styled HTML page otherwise.
  ``build_extension_zip`` refuses path-traversal entries (any
  segment containing ``..``, blank names, absolute paths) and
  skips ``__pycache__`` / ``.pyc`` / ``.swp`` / ``.tmp`` / ``.bak``;
  per-file size is capped at 5 MiB.  An ``INSTALL.md`` with the
  real Web UI origin is injected at build time so users do not
  have to edit the manifest.  A footer link in the Web UI
  surfaces the new page next to ``/api/health``.  24 new tests
  in
  [test_extension_download.py](file:///d:/1/video2text/douyin_batch/tests/test_extension_download.py)
  cover ZIP contents, path-traversal rejection, endpoint
  responses and rate-limit interaction.

### Security
- **Docker hardening** ([Dockerfile](file:///d:/1/video2text/Dockerfile)):
  the runtime image now runs as a dedicated non-root user
  (``video2text``, uid 1000), declares an inline ``HEALTHCHECK``
  that probes ``/api/health`` every 30 s, and pip uses
  ``--no-cache-dir`` in the builder.  A subtle bug in the previous
  ``chmod 777 /workspace`` was also fixed: the directory is now
  owned by the service account from the start.
- **SourceRef typing** ([models.py](file:///d:/1/video2text/video2text/models.py)):
  introduced ``SourceKind = Literal[...]`` and the
  ``is_known_kind`` property so downloader dispatch tables can
  rely on a closed set without breaking on unknown URLs.
- **Web app rate limiting** ([web/app.py](file:///d:/1/video2text/video2text/web/app.py)):
  ``/api/transcribe`` is now protected by a per-client sliding-window
  rate limiter (default 10 requests / 60 s, configurable via
  ``VIDEO2TEXT_RATE_LIMIT`` and ``VIDEO2TEXT_RATE_LIMIT_WINDOW``,
  disable with ``=0``).  Rejected requests return ``HTTP 429`` with
  ``Retry-After`` and ``X-RateLimit-*`` headers (RFC 6585).
  ``/api/health`` reports the active configuration so dashboards
  can surface a banner.

### Fixed
- **MCP `transcribe_video` tool** ([mcp_server.py](file:///d:/1/video2text/video2text/mcp_server.py))
  used an invented ``Pipeline(source=..., language=..., output_dir=..., whisper_model=...)``
  constructor and a non-existent ``Pipeline.run()`` method.  The tool
  now builds a real ``Settings``, instantiates ``Pipeline``, and
  calls ``Pipeline.transcribe(source_input=...)``.  Three new tests
  in ``test_mcp_server.py`` lock the contract in place.
- **MCP `get_transcript` path-traversal** ([mcp_server.py](file:///d:/1/video2text/video2text/mcp_server.py)):
  any caller could ask for ``/etc/passwd`` or ``~/.ssh/id_rsa``.
  The tool now pins reads to a configurable
  ``VIDEO2TEXT_TRANSCRIPT_ROOT`` (default ``./output/transcripts``),
  refuses symlink escapes via ``Path.relative_to``, blocks files
  larger than 5 MiB, and rejects non-UTF-8 bytes.
- **Web app auth + CORS** ([web/app.py](file:///d:/1/video2text/video2text/web/app.py)):
  ``/api/transcribe`` now requires a ``Bearer`` token when
  ``VIDEO2TEXT_API_TOKEN`` is set (constant-time comparison via
  ``hmac.compare_digest``).  CORS middleware pre-allows
  ``chrome-extension://<id>``, ``moz-extension://<id>``,
  ``file://``, ``http(s)://(localhost|127.0.0.1|host.docker.internal)``
  via a regex matcher, with the explicit ``VIDEO2TEXT_CORS_ORIGINS``
  env var to override.
- **Flaky OCR partial-success test**:
  ``test_ocr_images_partial`` asserted an order-dependent list from
  a ``ThreadPoolExecutor``.  Replaced with ``assertCountEqual``.
- **Web app job-id collision risk**: ``__import__('time').time()`` +
  ``abs(hash(url)) % 10**8`` replaced with top-level ``time`` +
  ``uuid.uuid4().hex[:8]``.
- **`safe_stem` Windows-hostile regex** ([inputs.py](file:///d:/1/video2text/video2text/inputs.py)):
  the previous ``re.sub(r'[^\w\-_]', '_', name)`` left Windows
  reserved names (``CON``, ``PRN``, ``COM1``…) and control characters
  untouched, which could crash downloads on titles like
  ``report:2024`` or ``v?.mp4``.  ``safe_stem`` now delegates to
  ``douyin_batch.platform_compat.safe_filename`` so the same
  sanitisation rules apply across the CLI, the Web UI and the
  MCP ``safe_filename`` tool.

### CI
- **Dependabot** ([.github/dependabot.yml](file:///d:/1/video2text/.github/dependabot.yml)):
  weekly PRs for ``pip``, ``github-actions`` and ``docker``
  ecosystems, with grouped bumps for the ``whisper``, ``media``
  and ``ml`` dependency stacks so the queue stays reviewable.
- **Bandit security scan** ([.github/workflows/bandit.yml](file:///d:/1/video2text/.github/workflows/bandit.yml)):
  weekly + on-PR scan of ``video2text/`` and ``douyin_batch/``
  with the test tree excluded.  Reports uploaded as build
  artifacts and printed to the workflow log; ``-ll`` threshold
  keeps the signal-to-noise ratio high.

### Added
- **One-click Web UI launcher** (`scripts/one_click_up.py` + `.sh` + `.ps1`):
  cross-platform daemon-style launcher that starts the FastAPI server
  in the background, polls `/api/health` for 30 s, opens the browser,
  and prints the browser-extension install hints.  Sub-commands
  `up` / `stop` / `status`; flags `--daemon`, `--no-browser`,
  `--mode docker`, `--reload`, `--host`, `--port`.  PID and log
  files at the project root (`.one-click.pid`, `.one-click.log`).
- **Docker support** (`Dockerfile` + `docker-compose.yml`): multi-stage
  build (`python:3.11-slim` + ffmpeg + tini) that ships a small
  runtime image.  Compose service exposes port 8000, mounts
  `./web-workspace` for transcript persistence, and includes an
  HTTP healthcheck.  Optional GPU passthrough via the commented
  `deploy.resources` block.
- **Make / just convenience targets** (`Makefile` + `justfile`):
  `up`, `up-daemon`, `down`, `status`, `logs`, `docker-up` — each
  delegates to the Python launcher.
- **Browser extension Docker host permission**: `extension/manifest.json`
  now lists `http://host.docker.internal/*` so the extension can
  reach a containerised Web UI from the host browser.
- **One-click launch docs** (`docs_site/one-click.md`): TL;DR for
  Linux/macOS, Windows and Docker; full lifecycle command table;
  Docker specifics; troubleshooting matrix.  Linked from the
  mkdocs nav.
- **One-click tests** (`douyin_batch/tests/test_one_click.py`,
  17 cases): launcher module API, sub-commands, idempotent
  stop, shell wrapper existence + AST parse, PowerShell wrapper
  parse, Docker artefacts, Makefile / justfile targets, extension
  manifest, mkdocs nav, documentation page.
- **Plugin system** (`video2text.plugins`): third-party `Downloader` /
  `Transcriber` / `URLTransformer` registration via `entry_points`.
  Includes hookspecs, an in-tree example (`examples/plugins/`), and
  a `clear_cache()` helper for tests.
- **Long-video chunking** (`video2text.transcribers.chunked`):
  `ChunkedTranscriber` splits long audio into overlapping windows
  (default 600 s + 5 s overlap) and merges per-chunk segments with
  global timestamps.  Includes `probe_duration`, `split_audio`,
  `merge_texts`, `merge_segments` helpers.
- **Performance utilities** (`video2text.performance`):
  `@profile_step` decorator, `PerformanceReport` (with
  mean / min / max / total per step), `parallel_map` (thread pool
  mapper), and `DownloadCache` (URL → path single-run cache).
- **Observability mini-SDK** (`video2text.observability`):
  zero-dependency OpenTelemetry-compatible `Tracer` / `Meter`
  that records to an in-memory `OBSERVABILITY` dict.  One call
  (`install_opentelemetry_exporter`) upgrades to the real
  `opentelemetry-sdk` when available.
- **Browser extension** (`extension/`): Chrome / Edge Manifest v3
  popup that POSTs the active tab URL to a self-hosted Web UI.
  Bundled icons (16/48/128), options page, service worker.
- **Web UI** (`video2text.web`): FastAPI + vanilla HTML app
  exposed via `python -m video2text.web.app`.  Pydantic-validated
  `/api/transcribe` endpoint.  Pyproject `[web]` extra.
- **E2E recording script** (`scripts/capture_e2e_recording.py`):
  bundles real-URL E2E + WeChat MP E2E into a self-contained
  timestamped package with a top-level `README.md` and
  `SUMMARY.json`, ready to attach to a GitHub release.
- **mkdocs documentation site** (`docs_site/` + `mkdocs.yml`):
  18 pages including getting-started, platforms, engines,
  plugins, WeChat MP, web UI, browser extension, MCP server,
  benchmarking, chunking, performance, observability, E2E
  testing, CI matrix, changelog, contributing, license.  Material
  theme with light/dark palette toggle.  GitHub Pages workflow
  in `.github/workflows/docs.yml`.
- **CI matrix** (`.github/workflows/test.yml`): 5 Python × 3 OS
  = 15 cells.  Separate `lint`, `coverage`, `build-docs` jobs
  with concurrency cancellation.  System ffmpeg install step per OS.
- **Three SKILL.md files** under `.trae/skills/` for agent
  integration: `video2text`, `video2text-wechat`, `video2text-benchmark`.

### Fixed
- WeChat MP detector now accepts both `/s?__biz=` and `/s/<id>?__biz=`
  URL forms (the latter is the more common in-the-wild shape).

### Tests
- 240+ tests passing (was 232).  New test files:
  - `test_wechat_mp_detector.py` (4 tests)
  - `test_benchmark_transcribers.py` (5 tests)
  - `test_web_app.py` (4 tests)
  - `test_extension_manifest.py` (6 tests)
  - `test_skill_frontmatter.py` (3 tests)
  - `test_plugin_system.py` (10 tests)
  - `test_chunked_transcriber.py` (5 tests)
  - `test_performance_utilities.py` (9 tests)
  - `test_observability.py` (8 tests)
  - `test_docs_site.py` (11 tests)

## [2.1.0] - 2026-05-15

### Added
- `douyin_batch` library: logger, config, cache, retry, progress, report
- Smart downloader routing (`DouyinDownloader` vs `YtDlpDownloader`) without cookies
- Markdown output with auto-segmented paragraphs and metadata header

### Changed
- Default transcription engine: OpenAI Whisper
- Output format unified to `.md` (Markdown)

## [2.0.0] - 2026-04-20

### Added
- Initial integration of yt-dlp, bili2text, WhisperX, whisper.cpp
- Support for Bilibili and Douyin platforms
- CLI entry point `python -m video2text`

### Known Limitations
- Douyin required cookies in early versions
- Markdown output added in 2.1.0


## [Work Log] — video2text 时代累积开发日志（v3.1.0 开发周期，未随版本归档）

> 2026-10-03 重排说明：本节是改名前遗留的两个 Unreleased 之一，按原文保留、
> 条目顺序未动；内容对应 v3.1.0 前后的开发期（测试 38 → 547 的各轮覆盖补齐、
> MCP server 与 YouTube / 小红书 / 公众号下载器首发、CI 矩阵拆分等），
> 未逐条考证归入具体版本，故作为附录置于版本时间线之后。

### Added
- **AI agent compatibility**: `AGENTS.md`, `CLAUDE.md`, `.cursorrules`,
  `.clinerules`, `.windsurfrules`, `.github/copilot-instructions.md`,
  `.cody.yml`, `.continue/config.json`, `.aider.conf.yml`, `.traerules`
- **MCP (Model Context Protocol) server**: `python -m video2text.mcp_server`
  with 8 tools (`transcribe_video`, `batch_transcribe_creator`,
  `get_cache_stats`, `validate_url`, `sanitize_filename`, `detect_platform`,
  `get_transcript`, `transcribe_wechat_mp`) over stdio or HTTP
- **`--json` output mode** for `douyin_batch_v3.py` with stable schema
  `video2text.agent-output/v1` (see `douyin_batch/agent_output.py`)
- **`--bilingual-json` flag** to attach localised human labels alongside
  stable English constants (`platform_label`, `status_label`, `stage_label`)
- **`--platform` filter** on `douyin_batch_v3.py` (repeatable, e.g.
  `--platform youtube --platform wechat_mp`) to restrict the batch to
  a whitelist of platforms; non-matching videos return `status=skipped`
- **`Makefile`** + **`justfile`** with agent-friendly targets:
  `make install`, `make test`, `make lint`, `make format`, `make verify`
- Bilingual (English / Chinese) UI for `douyin_batch_v3.py` via the `i18n` module
- New `--lang {en,zh}` CLI flag with auto-detect default
- `douyin_batch/security.py` — URL/filename safety utilities
- `douyin_batch/platform_compat.py` — cross-platform helpers (FFmpeg, paths, filenames)
- Cross-platform unit tests (Windows / macOS / Linux path handling, ffmpeg detection)
- `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `.editorconfig`, `.gitignore`
- GitHub Actions CI matrix (Python 3.8–3.12 on Ubuntu / macOS / Windows)
- Issue & pull-request templates
- **YouTube downloader** (`YouTubeDownloader`) — wraps `yt-dlp` with tuned
  `player_client` rotation (web_safari / ios / android / web_embedded) to
  dodge 403 / SABR / login walls
- **Xiaohongshu downloader** (`XiaohongshuDownloader`) — uses Playwright to
  extract real media URLs from explore / discovery / xhslink short links;
  handles video notes and image notes
- **WeChat MP downloader** (`WechatMpDownloader`) — pulls `mp.weixin.qq.com`
  articles, extracts `#js_content` text and meta, and (for video messages)
  downloads the embedded mp4. Text articles skip ASR; video messages go
  through the standard pipeline
  - 3-mode dispatcher: text article, video message, image article
  - **OCR engine fallback chain** (`paddleocr` → `pytesseract` → `easyocr`)
    with `ocr_engine` / `ocr_lang` / `save_images` options
  - 3-tier cookie injection (dict > file > env var)
  - **Bilingual subtitle output** for video messages when
    `Pipeline.transcribe(..., bilingual=True)` is set
- **`DOWNLOADERS.md`** — per-platform reference, fallback chain, and
  instructions for adding new downloaders
- **`*_label_i18n_lang` markers** in `AgentOutput` JSON to identify the
  locale used for each `*_label` field, plus a top-level `i18n_lang`
  marker on the whole document
- **`wechat_mp_status`** (success / partial) on the metadata to distinguish
  fully-extracted text articles from those with partial OCR failures
- **OCR engine selection** in `transcribe_wechat_mp` MCP tool:
  `ocr_engine` enum (auto / paddleocr / pytesseract / easyocr),
  `ocr_lang` (default `chi_sim+eng`), `save_images` flag
- **Ruff lint config** in `pyproject.toml` (replaces ad-hoc checks);
  `make lint` and `make verify` now run ruff with 0-error policy
- **Stage cancel mechanism (v3.2.0e)**: `PipelineCancelled` exception +
  `PipelineContext.raise_if_cancelled()` — each `Stage.run()` checks
  `cancel_event` before/after blocking operations (download / ffmpeg / ASR /
  LLM post-process / disk write); `run_with_progress` skips 100% emit on
  cancel. `AsyncPipeline.run_batch` wraps cancelled tasks into
  `_FailedResult(src, PipelineCancelled)` for caller-side distinction
- **GPU VRAM-aware concurrency (v3.2.0e)**: `_gpu_aware_concurrency()` caps
  `max_concurrent` by `free_vram // vram_per_task_mb` on CUDA; falls back to
  `base` on CPU / metal / unknown VRAM. `_GpuHealthCache` TTL cache (5s,
  thread-safe) avoids re-probing `gpu_health()` per batch.
  New env var `VIDEO2TEXT_VRAM_PER_TASK_MB` (default 3000, based on large-v3
  ~5GB / medium ~5GB / small ~2GB)

### Changed
- `douyin_batch_v3.py` is now fully i18n-aware (CLI help, banner, status messages)
- README is bilingual; English-first while preserving the Chinese section
- `pyproject.toml` updated with optional `whisperx` / `faster-whisper` extras
- `requirements.txt` and `requirements-dev.txt` aligned
- `Logger.set_quiet()` for clean `--json` mode output
- `AgentOutput._apply_labels` accepts a `lang` parameter; if given, the
  global i18n language is temporarily switched so the rendered labels
  actually match that locale (then restored)
- `Pipeline.transcribe` accepts a new `bilingual` keyword for WeChat MP
  video messages; the markdown output and JSON metadata carry i18n labels
- `process_single_video_safe` accepts a `platform_filter` list

### Fixed
- Hard-coded Chinese strings in `douyin_batch_v3.py` replaced with `t()` calls
- `safe_filename()` strips control chars and Windows reserved names consistently
- MCP server's stdio parser now handles UTF-8 BOM (some agents add it)
- `Pipeline._get_downloader` fallback chain ensures every platform gets
  a working downloader even if its dedicated class fails to import
- `Settings.wechat_cookies_file` is now properly stored as a `Path` after
  the CLI parses `--wechat-cookie-file`

### Tests
- 38 basic unit tests
- 14 agent-output tests (JSON schema, Unicode, save/load, etc.)
- 15 MCP server tests (JSON-RPC, stdio round-trip, tool dispatch, errors)
- 21 new downloader tests (YouTube / Xiaohongshu routing, URL detection, CDN regex)
- 20 pipeline router tests (fallback chain, WeChat MP text/video split)
- 33 i18n + cookies + MCP wechat_mp tests (platform labels, status/stage
  constants, cookie parsing, env injection, MCP tool dispatch)
- 29 bilingual + OCR + CLI tests (i18n bilingual mode, image extraction,
  OCR graceful degradation, WeChat CLI flags, platform detection)
- 20 round-5 tests (ocr_engine schema, video-article bilingual markdown,
  --platform filter, *_label_i18n_lang markers, WeChat MP download options)
- **190 tests total, all passing in ≈ 21 s**
- **Lint**: 0 ruff errors / 71 files compile / 0 syntax issues

### Tests (coverage补齐)
- 49 tests in `test_coverage_gaps.py` covering previously-untested branches:
  - `url_utils.extract_bvid` / `is_short_url` / `resolve_short_url` (incl.
    HTTP 403 fallback to identity), `normalize_bilibili_url`, `normalize_url`
  - `inputs.is_audio_file` / `is_video_file` / `safe_stem`
  - `chunked.merge_texts` / `merge_segments` / `probe_duration` WAV happy
    and failure paths
  - `performance.to_markdown` (empty + with steps),
    `PerformanceReport.write`, `parallel_map` (empty / ordered / errors),
    `DownloadCache` round-trip
  - `observability.set_status` / `record_exception`, nested spans sharing
    a `trace_id`, `meter.counter` / `meter.histogram`,
    `install_opentelemetry_exporter` unavailable returns `False`
  - Web app `/api/extension/install.md` HTML and raw markdown responses
  - `cache.ProcessCache` corrupt JSON, `mark` / `filter` / `user_videos` /
    `clear`
  - `retry.retry` with `max_retries=0`, 3-sleep backoff sequence
    `[0.1, 0.2, 0.4]`, `on_retry` callback, unrelated exception not
    retried
  - `security.safe_join_path` path-traversal attempts, `validate_video_id`,
    `check_url_safety`, `sanitize_filename` edge inputs
  - `platform_compat.safe_filename` / `normalize_path` / `check_ffmpeg` /
    `get_os` / `check_python_version`
- 33 tests in `test_coverage_gaps_2.py` filling deeper gaps:
  - `models.SourceRef.display_name` (three fallbacks), `is_known_kind`,
    `KNOWN_SOURCE_KINDS`, `TranscriptResult` defaults
  - `config.Settings.ensure_directories`, `_parse_cookie_string` (JSON and
    Netscape), `_load_cookie_file` (missing)
  - `observability.install_opentelemetry_exporter` returns `False` when
    SDK unavailable
  - `factory.get_transcriber` / fallback chain / unknown engine raises,
    `chunked.probe_duration` raises `RuntimeError` for unreadable mp3
  - `Downloader()` is abstract, `YouTubeDownloader.supports` true/false,
    `YtdlpDownloader` name + `_ydl is None`
  - `--help` exits 0; plugin registry `list_downloaders` /
    `list_transcribers` / `clear_cache`
  - i18n English/Chinese messages, format kwargs, unknown key returns
    `[key]`, `detect_language` / `init_language` / `get_language` round-trip
  - Logger singleton, `set_level` + `set_quiet`, `add_file_handler`
    auto-creates parent directory
  - `platform_compat.find_ffmpeg` returns `None` / `Path` depending on PATH
  - Retry backoff does not leak `jitter` / `delay` / `backoff` kwargs to
    wrapped function
- **82 new tests** this round. **473 tests passing, 9 skipped in ≈ 231 s**
- **Coverage**: 66% → 68% project-wide. Per-module highlights:
  - `cache.py` 84% → **100%**
  - `models.py` 91% → **100%**
  - `logger.py` 75% → 97%
  - `performance.py` 94% → 97%
  - `config.py` 95% → 97%
  - `security.py` 67% → 91%
  - `i18n.py` 92% → 93%
  - `url_utils.py` 45% → 86%
  - `inputs.py` 73% → 81%
  - `observability.py` 77% → 79%
  - `chunked.py` 73% → 77%
- **Lint**: 0 ruff errors / 0 syntax issues

### Tests (coverage round 2)
- 12 tests in `test_report_module.py` covering `report.generate_summary_report`:
  - Empty / all-success / all-failed / mixed results
  - `transcript` field is `None`, missing, or points at a non-existent file
    (each safely skipped instead of raising)
  - URL field fallback to constructed `https://www.douyin.com/video/{id}`
  - Unknown `status` counted as failure
  - Empty transcript body suppresses the "转录内容" block
  - JSON sidecar written with `ensure_ascii=False`
  - Status icons `✅` / `❌` rendered
  - Output directory created recursively if missing
- 7 tests in `test_transcribe_module.py` covering `transcribe.TranscriberPool`:
  - Singleton identity across multiple constructor calls
  - `_initialized` short-circuit (no second `Pipeline()` instantiation)
  - `__new__` returns the pre-set instance even if `__init__` is bypassed
  - `transcribe()` returns `transcript_path` on success, `None` on exception
  - Failure path prints "转录失败: …" with the underlying error
  - Module-level `transcribe_audio()` delegates to the pool, propagates
    `None` on failure
- 18 tests in `test_browser_module.py` covering `browser.py` (all
  Playwright interactions mocked, no real browser launched):
  - `BrowserManager` singleton + `new_page` / `close` lifecycle
  - `close()` swallows exceptions from `_context` / `_browser` / `_playwright`
  - `get_user_url_from_video`: matches the JSON `sec_uid` pattern, the query
    string pattern, picks the first hit across polls, returns `None` when
    no match, returns `None` on `goto` exception
  - `get_user_videos`: collects multiple unique videos, deduplicates, caps
    at `max_videos`, returns `[]` on `goto` exception, stops when no new
    videos appear in 2 consecutive rounds
  - `get_media_url_fast`: captures `media-audio.douyinvod.com` URLs,
    ignores non-audio `douyinvod.com` responses, returns `None` on no
    capture and on `goto` exception
- **37 new tests** this round. **510 tests passing, 9 skipped in ≈ 241 s**
- **Coverage**: 71.7% → **89.2%** project-wide. Per-module highlights:
  - `report.py` 10.4% → **100%**
  - `transcribe.py` 33.3% → **100%**
  - `browser.py` 11.0% → 92.4%
  - `cache.py` 100% / `models.py` 100% (retained)
- **Bug fix**: `report.generate_summary_report` previously crashed with
  `TypeError` when a successful result had `transcript=None` and with
  `PermissionError: '.'` when the `transcript` key was missing. Now skips
  such rows cleanly while still counting them in the success total.
- **Lint**: 0 ruff errors / 0 syntax issues

### Tests (coverage round 3)
- 37 tests in `test_coverage_gaps_3.py` covering the remaining <70% modules:
  - `retry.download_media_with_retry`: success first try, success after
    retries (correct sleep count), final failure returns `False` with
    "下载最终失败" print, parent directory auto-created, on_retry callback
    prints "⚠️ 第 N 次重试", empty `iter_content` chunks filtered out
  - `config.BatchConfig`: `from_env` overrides defaults for all 9 env vars,
    yes/no truthy parsing, missing env keeps defaults, `merge_cli_args`
    for num / workers / no_headless / retries, `save` creates deep parent
    dirs with `ensure_ascii=False`, `__str__` lists every field,
    `from_dict` filters unknown keys, `from_file` on missing returns
    defaults
  - `platform_compat.find_ffmpeg`: PATH hit, common-path hit, no hit,
    unknown OS
  - `platform_compat.install_ffmpeg_instructions`: Windows (gyan +
    choco + scoop), macOS (brew + port), Linux (apt + dnf + pacman),
    unknown fallback
  - `platform_compat.open_file_location`: Windows `explorer /select,`,
    macOS `open -R`, Linux `xdg-open <parent>`, subprocess failure
    returns `False`
  - `platform_compat.get_temp_dir`, `normalize_path` (`expanduser` +
    `resolve`), `get_os` branches (Linux / Darwin / Windows / unknown),
    `get_python_info` includes `ffmpeg_available`
- **37 new tests** this round. **547 tests passing, 9 skipped in ≈ 175 s**
- **Coverage**: 89.2% → **96.6%** project-wide. Per-module highlights:
  - `retry.py` 54.1% → **100%**
  - `config.py` 63.0% → **100%**
  - `platform_compat.py` 70.7% → **100%**
  - **7 modules at 100%**: `__init__`, `agent_output`, `cache`, `config`,
    `platform_compat`, `report`, `retry`, `transcribe`
- **Lint**: 0 ruff errors / 0 syntax issues

### CI (matrix split)
- Reworked [`.github/workflows/test.yml`](.github/workflows/test.yml) into
  six independent jobs that can run in parallel:
  - `lint` — ruff check + format check (≈ 15 s)
  - `unit` — py3.11 + ubuntu, skips `integration` / `network` markers
    (≈ 60 s) — this is the **PR feedback path**
  - `integration` — py3.9/3.11/3.12 × ubuntu/windows, runs only on push
    to main, tag push, manual dispatch, or nightly cron (UTC 02:00)
  - `matrix` — full 5 py × 3 OS, runs only on tag push (release gate)
    or develop merges
  - `coverage` — py3.11 + ubuntu, `--cov-fail-under=60` → `=90` to
    match the 97 % project coverage
  - `build-docs` — soft check, non-blocking
- Added `markers` declaration to `[tool.pytest.ini_options]`: `integration`,
  `network`, `slow`. Tests can opt in via `@pytest.mark.integration`.
- **PR feedback time**: from ≈ 5 min (full 5×3 matrix) to ≈ 30 s
  (lint + unit).
- Bumped coverage gate: `--cov-fail-under=60` → `=90`.

### Release (v3.1.0 tag)
- Initialised video2text as a standalone git repo (was a subdirectory of
  `D:/1`). Created branch `main` with 199 files in the first commit.
- Annotated tag `v3.1.0` pointing at the release commit.
- New files:
  - [GITHUB_RELEASE_v3.1.0.md](GITHUB_RELEASE_v3.1.0.md) — full Markdown
    release description ready to paste into GitHub Releases.
  - [PUBLISH_v3.1.0.md](PUBLISH_v3.1.0.md) — step-by-step push +
    release guide (web UI + `gh` CLI options).
  - [docs_site/roadmap-v3.2.md](docs_site/roadmap-v3.2.md) — 4-tier
    v3.2.0 plan covering VAD chunking, persistent caches, profile CLI
    透出, CI matrix split (done), WhisperX default, plugin CLI,
    marketplace page, WebSocket progress, transcript diff, and
    multi-engine consensus.
- `mkdocs.yml` adds `Roadmap v3.2` to the nav.


<!-- 版本对比链接(Keep a Changelog); 2.x 早于打 tag 时代, 无对应 tag -->
[unreleased]: https://github.com/1273984347/mediascribe/compare/v3.3.0...HEAD
[3.3.0]: https://github.com/1273984347/mediascribe/compare/v3.2.0a...v3.3.0
[3.2.0a]: https://github.com/1273984347/mediascribe/compare/v3.1.0...v3.2.0a
[3.1.0]: https://github.com/1273984347/mediascribe/releases/tag/v3.1.0
