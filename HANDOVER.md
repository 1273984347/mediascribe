# Video2Text 项目交接文档

> **版本**: v3.2.0e (开发中, 未提交)
> **日期**: 2026-07-24
> **测试状态**: 772 passed, 14 skipped, 0 failed
> **当前状态**: v3.2.0c 三项任务 + v3.2.0d LLM 后处理骨架 + 16 个抖音视频转录 + v3.2.0e cancel 机制 + GPU 显存感知并发均已完成, 所有改动未 commit

---

## 一、项目概述

### 1.1 项目定位

Video2Text 是一个面向 GitHub 全球开源的**离线视频转录工具**，支持抖音、B站、YouTube、小红书、微信公众号等多平台视频转文字。核心使用 OpenAI Whisper / faster-whisper / WhisperX 引擎，支持 GPU 加速和说话人分离。

### 1.2 核心特性

- **多平台下载**: 抖音(Playwright 自动化)、B站/YouTube(yt-dlp)、小红书、微信公众号
- **多引擎转录**: openai-whisper / faster-whisper / whisperx，自动选择
- **GPU 加速**: 自动检测 CUDA / Metal / ROCm，RTX 4060 实测可用
- **ASR 自动学习**: 从用户校对中累积术语库，越用越准 (v3.2.0b 新增)
- **异步批处理**: `AsyncPipeline` 支持并发转录 + 单视频失败隔离
- **Web Dashboard**: FastAPI Web UI + WebSocket 实时进度 + 速率限制
- **MCP 工具**: Model Context Protocol 服务器，供 AI agent 调用
- **Chrome 扩展**: Side Panel 支持，一键 ZIP 下载

### 1.3 技术栈

| 层 | 技术 |
|----|------|
| 转录引擎 | openai-whisper, faster-whisper, whisperx |
| 下载 | yt-dlp, Playwright (抖音) |
| Web | FastAPI + Jinja2 + WebSocket |
| 异步 | asyncio + asyncio.to_thread |
| GPU | PyTorch 2.6.0+cu124 |
| 测试 | pytest (707 tests), ruff |
| CI/CD | GitHub Actions (lint/test/build/publish) |
| 打包 | pyproject.toml + setuptools |
| 容器 | Docker (非 root 用户 + healthcheck) |

---

## 二、项目架构

### 2.1 目录结构

```
video2text/
├── video2text/                    # 主包
│   ├── __init__.py                # 公共 API 导出
│   ├── __main__.py                # CLI 入口 (transcribe/batch/profile/learn)
│   ├── config.py                  # Settings 配置类
│   ├── pipeline.py                # Pipeline 同步管道 + GPU 解析
│   ├── pipeline_async.py          # AsyncPipeline 异步管道 (v3.2.0b)
│   ├── pipeline_stages.py         # Stage 链 (Parse/Download/Extract/Transcribe/Assemble)
│   ├── models.py                  # 数据模型 (SourceRef, TranscriptResult 等)
│   ├── inputs.py                  # parse_source 源类型识别
│   ├── audio_utils.py             # ffmpeg 音频提取
│   ├── cache.py                   # 持久化缓存 (XDG/LOCALAPPDATA)
│   ├── learn.py                   # ASR 自动学习模块 (v3.2.0b 新增)
│   ├── post_process.py            # 术语校正 + Prompt 模板 + 模型推荐
│   ├── performance.py             # 性能分析装饰器
│   ├── profile_cli.py             # profile CLI 子命令
│   ├── progress.py                # WebSocket 进度注册表
│   ├── observability.py           # 日志/metrics
│   ├── mcp_server.py              # MCP 工具服务器
│   ├── url_utils.py               # URL 规范化
│   │
│   ├── downloaders/               # 下载器
│   │   ├── base.py                # Downloader 抽象基类
│   │   ├── douyin.py              # 抖音 (Playwright 浏览器自动化)
│   │   ├── youtube.py             # YouTube (yt-dlp)
│   │   ├── ytdlp.py               # B站等 (yt-dlp 通用)
│   │   ├── xiaohongshu.py         # 小红书
│   │   └── wechat_mp.py           # 微信公众号
│   │
│   ├── transcribers/              # 转录引擎
│   │   ├── base.py                # Transcriber 抽象基类
│   │   ├── factory.py             # 引擎工厂 (whisper/faster-whisper/whisperx)
│   │   ├── whisper.py             # openai-whisper (含 torch 2.6 兼容补丁)
│   │   ├── faster_whisper.py      # faster-whisper (CTranslate2)
│   │   ├── whisperx.py            # WhisperX (说话人分离)
│   │   └── chunked.py             # VAD 分块转录 (>2h 长视频)
│   │
│   ├── web/                       # Web 服务
│   │   ├── app.py                 # FastAPI 应用 (含速率限制/CORS/认证)
│   │   ├── extension_builder.py   # Chrome 扩展 ZIP 打包
│   │   └── static/index.html      # Dashboard 前端 (Vanilla JS)
│   │
│   └── plugins/                   # 插件系统
│       └── registry.py            # 插件注册表
│
├── douyin_batch/                  # 批处理 + i18n
│   ├── i18n.py                    # 中英双语 (t(key, **kwargs) 调用)
│   └── tests/                     # 721 测试
│
├── extension/                     # Chrome 扩展
├── docs_site/                     # MkDocs 文档站
├── scripts/                       # 工具脚本
│   ├── benchmark_transcribers.py  # 跨引擎 benchmark CLI
│   └── benchmark_corpus/          # benchmark 语料库
│
├── .github/workflows/             # CI/CD
│   ├── test.yml                   # 矩阵测试 (Python 3.8-3.12 × Win/Mac/Linux)
│   ├── release.yml                # PyPI + Docker Hub + GitHub Release
│   └── security.yml               # Bandit 安全扫描
│
├── archive/                       # 归档 (开发遗留, gitignore)
│   ├── legacy-scripts/
│   ├── legacy-tests/
│   ├── temp-data/
│   ├── release-docs/
│   ├── ai-config/
│   └── temp-scripts/
│
├── pyproject.toml                 # 包配置
├── Dockerfile                     # 容器 (非 root + healthcheck)
├── docker-compose.yml             # restart: unless-stopped
├── Makefile / justfile            # 一键启动
├── main.py                        # Web 服务启动入口
├── CHANGELOG.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── LICENSE (MIT)
└── README.md
```

### 2.2 核心数据流

```
用户输入 (URL/文件)
    │
    ▼
parse_source() ──▶ SourceRef {kind, url, path}
    │                  kind ∈ {douyin, bilibili, youtube,
    │                          xiaohongshu, wechat_mp, video, audio}
    │
    ▼
Pipeline.transcribe()
    │
    ├─ ParseSourceStage    ──▶ ctx.source = SourceRef
    ├─ DownloadStage       ──▶ ctx.download_result (本地文件或 wechat_mp 文本)
    │     ├─ DouyinDownloader (Playwright)
    │     ├─ YtDlpDownloader (bilibili/youtube)
    │     ├─ XiaohongshuDownloader
    │     └─ WechatMpDownloader (文本型, 不走转录)
    ├─ ExtractAudioStage   ──▶ ctx.audio_path (ffmpeg 提取, 音频文件旁路)
    ├─ TranscribeStage     ──▶ ctx.transcription, ctx.text
    │     ├─ auto_prompt (领域 Prompt + 学习术语注入)
    │     └─ engine_name 写入 ctx
    └─ AssembleStage       ──▶ TranscriptResult
          └─ post_process_transcript() (术语校正 + 学习术语)
```

### 2.3 关键设计决策

1. **Stage 链而非单体函数** — `Pipeline.transcribe()` 内部调用 5 个 `Stage` 子类,通过 `PipelineContext` 流动状态。便于 v3.2.0b 异步化。
2. **PipelineContext 而非 kwargs** — dataclass 累加中间结果,避免 10+ 参数传递。
3. **同步 + thread-friendly** — `Stage.run()` 是同步方法,`AsyncPipeline` 用 `asyncio.to_thread` 包装。
4. **本地文件旁路** — 本地视频文件在 `DownloadStage` 直接设 `download_result.path`,音频文件跳过 `ExtractAudioStage`。
5. **引擎默认 whisper** — 国内 HuggingFace 网络不稳定,`faster-whisper` 首次下载模型可能超时。用户可通过 `VIDEO2TEXT_ENGINE=faster-whisper` 切换。
6. **术语库持久化路径** — 使用 `persistent_cache_dir()`(XDG/LOCALAPPDATA),不写入源码目录(pip install 后不可写)。

---

## 三、当前开发状态

### 3.1 版本里程碑

| 版本 | 状态 | 关键交付 |
|------|------|---------|
| v3.1.0 | ✅ 已发布 | 多平台下载 + Whisper 转录 + Chrome 扩展 + Web UI |
| v3.2.0a | ✅ 已发布 (tag `v3.2.0a`) | VAD 分块 + 持久缓存 + profile CLI + WebSocket 进度 |
| v3.2.0b | ✅ 开发完成 (未提交) | 异步 Pipeline + GPU 加速 + benchmark + ASR 自动学习 |
| v3.2.0c | ✅ 开发完成 (未提交) | profile 装饰器 + UI GPU pill + cancel WS 桥接 + 隐性内存泄漏修复 + REST 404 风险 + DeprecationWarning |
| v3.2.0d | ✅ 开发完成 (未提交) | LLM 后处理工程化骨架 + 模型选择扩展 (10 个 Whisper 模型) + 16 个抖音视频转录实战 |
| v3.2.0e | ✅ 开发完成 (未提交) | Stage cancel 机制 (`PipelineCancelled` + `raise_if_cancelled`) + GPU 显存感知并发 (`_gpu_aware_concurrency` + `_GpuHealthCache` TTL 缓存) |
| v3.3.0 | 📋 规划中 | 默认引擎 = benchmark 优胜者 + LLM 真实 API 联调 |

### 3.2 v3.2.0b 已完成

| 功能 | 状态 | 关键文件 |
|------|------|---------|
| AsyncPipeline | ✅ | [pipeline_async.py](file:///d:/1/video2text/video2text/pipeline_async.py) |
| Stage 链解耦 | ✅ | [pipeline_stages.py](file:///d:/1/video2text/video2text/pipeline_stages.py) |
| GPU 设备解析 | ✅ | [pipeline.py](file:///d:/1/video2text/video2text/pipeline.py) `resolve_device()` + `gpu_health()` |
| /api/health GPU | ✅ | [web/app.py](file:///d:/1/video2text/video2text/web/app.py) |
| 抖音 Playwright 下载 | ✅ | [downloaders/douyin.py](file:///d:/1/video2text/video2text/downloaders/douyin.py) |
| HF 国内镜像 | ✅ | [post_process.py](file:///d:/1/video2text/video2text/post_process.py) `setup_hf_mirror()` |
| 术语校正 + Prompt | ✅ | [post_process.py](file:///d:/1/video2text/video2text/post_process.py) |
| 智能模型推荐 | ✅ | [post_process.py](file:///d:/1/video2text/video2text/post_process.py) `auto_select_model()` |
| ASR 自动学习 v2 | ✅ | [learn.py](file:///d:/1/video2text/video2text/learn.py) |
| Pipeline 自动后处理 | ✅ | [pipeline_stages.py](file:///d:/1/video2text/video2text/pipeline_stages.py) `AssembleStage` |
| CLI learn 子命令 | ✅ | [__main__.py](file:///d:/1/video2text/video2text/__main__.py) |
| benchmark CLI | ✅ | [scripts/benchmark_transcribers.py](file:///d:/1/video2text/scripts/benchmark_transcribers.py) |
| Release CI/CD | ✅ | [.github/workflows/release.yml](file:///d:/1/video2text/.github/workflows/release.yml) |
| torch 2.6 兼容 | ✅ | [transcribers/whisper.py](file:///d:/1/video2text/video2text/transcribers/whisper.py) |

### 3.3 v3.2.0c 已完成 (2026-07-21)

| 功能 | 状态 | 关键文件 |
|------|------|---------|
| profile 装饰器 | ✅ | `Pipeline(profile=True)` 包装各 stage,输出 JSONL 性能报告 |
| UI GPU pill | ✅ | 前端 Dashboard GPU 状态徽章 (读 `/api/health`) |
| cancel WS 桥接 | ✅ | 浏览器 cancel → WebSocket → `AsyncPipeline.cancel()` |
| 隐性内存泄漏修复 | ✅ | ThreadPoolExecutor 未 shutdown 问题 |
| REST 404 风险修复 | ✅ | 路由顺序问题 |
| DeprecationWarning 修复 | ✅ | asyncio API 升级 |
| 文档漂移修复 | ✅ | retrospective.md + project_memory.md 同步 |

### 3.4 v3.2.0d 已完成 (2026-07-21)

| 功能 | 状态 | 关键文件 |
|------|------|---------|
| LLM 后处理工程化骨架 | ✅ | [video2text/llm_post_process.py](file:///d:/1/video2text/video2text/llm_post_process.py) |
| 模型选择扩展 (10 个 Whisper 模型) | ✅ | CLI `argparse.choices` + Web API `Field(pattern)` + Web UI `<option>` 三处同步 |
| 模型分组 (快速预览/准确率优先/平衡) | ✅ | tiny/base/small · medium/large-v3 · distil-large-v3 |
| 16 个抖音视频转录实战 | ✅ | `output-test/transcripts/` 下 16 个 md 文件 (large-v3 + LLM 手工后处理) |

### 3.5 未提交的改动 (v3.2.0b + v3.2.0c + v3.2.0d 全量)

**重要**: 以下改动已通过测试但尚未 git commit (用户指示不 commit):

- v3.2.0b 全量改动 (见 3.2)
- v3.2.0c 三项任务 + 4 项 P1 修复
- v3.2.0d LLM 后处理骨架 + 模型扩展
- `output-test/transcripts/` 下 16 个转录 md 文件 (已去前缀,纯中文主题名)

### 3.6 P3 backlog (未阻塞当前版本)

- LLM 后处理流式输出 (长文本 30s+ 等待)
- 按时间戳分段独立调用 LLM
- 真实 API 联调测试 (需用户提供 key)
- Web UI 显示后处理状态标签 (解析 md 头部 banner)
- CLI dispatcher 全局选项修复 (`--workspace` 报 unknown command)

---

## 四、ASR 自动学习模块详解 (v3.2.0b 核心新功能)

### 4.1 设计理念

从用户校对中**自动累积**术语库,而非手动配置。转录越多次,专有名词识别越准。

### 4.2 数据模型

```python
@dataclass
class Correction:
    wrong: str           # ASR 错误文本
    right: str           # 正确文本
    count: int = 1       # 出现次数
    confirmed: bool      # count ≥ 2 自动确认,或手动确认
    contexts: List[str]  # 上下文示例 (最多 5 条)
    source: str          # 来源 (视频文件名)
```

### 4.3 核心 API

| 函数 | 用途 |
|------|------|
| `compare(reference, transcript)` | difflib 对比提取错误映射 |
| `learn_from_edit(original, corrected)` | diff 用户编辑前后的文本 (推荐入口) |
| `learn(pairs)` | 累积到术语库 |
| `get_learned_terms()` | 获取已确认术语 `{wrong: right}` |
| `get_prompt_terms()` | 生成 Whisper prompt 自然语言句子 |
| `confirm(wrong, right)` | 手动确认 |
| `remove(wrong, right)` | 删除 |
| `export_terms(path)` / `import_terms(path)` | 导出/导入 |

### 4.4 拼音相似度过滤

`compare()` 内部用 `pypinyin` 计算拼音编辑距离,只保留语音相似的替换 (ASR 典型错误模式),过滤语义替换噪音。

### 4.5 生效条件

- `count ≥ 2` → 自动确认
- 手动 `confirm()` → 立即生效
- 未确认的校正不参与替换

### 4.6 术语库位置

```
Windows: C:\Users\{user}\AppData\Local\video2text\Cache\learned_terms.json
Linux:   ~/.cache/video2text/learned_terms.json
macOS:   ~/Library/Caches/video2text/learned_terms.json
```

### 4.7 CLI 用法

```bash
# 对比学习
python -m video2text learn compare -r "霍去病是名将" -t "获取病是名将"

# 手动保存
python -m video2text learn save -w "获取病" -r "霍去病"

# 查看所有术语
python -m video2text learn list

# 确认待定术语
python -m video2text learn confirm -w "祥林扫" -r "祥林嫂"

# 清空
python -m video2text learn clear
```

### 4.8 Pipeline 集成

- `TranscribeStage` 自动注入学习术语到 Whisper prompt
- `AssembleStage` 写入 Markdown 前自动调用 `post_process_transcript()`
- `post_process_transcript()` 自动加载学习术语,优先级: `DEFAULT_TERMS < learned < env < custom`

---

## 四点五、LLM 后处理工程化骨架 (v3.2.0d 核心新功能)

### 4.5.1 设计理念

在 ASR 转录完成后,调用 LLM 对原始转录文本做后处理,修正专有名词 / 同音字 / 标点 / 分段,但**不改原话**、不润色文辞。失败时优雅降级返回原文 + `llm-failed` banner,不阻塞 pipeline。

### 4.5.2 核心设计原则

- **不改原话**:只修错字/标点/分段,不增删内容、不润色文辞
- **错误兜底**:API 失败返回原文 + `llm-failed` banner,不阻塞 pipeline
- **显式开关**:默认禁用,通过 env/Settings 启用
- **可观测**:md 头部加 `<!-- post-process: llm-reviewed -->` 标签
- **温度 0.0**:避免模型润色,保证输出确定性

### 4.5.3 模型选择扩展 (10 个 Whisper 模型)

| 分组 | 模型 | 10 分钟视频转录 | 显存 | 典型错字率 | 推荐场景 |
|---|---|---|---|---|---|
| 快速预览 | tiny / base / small (默认) | ~60s | ~2GB | 高 (~30 错字) | 快速预览 |
| 准确率优先 | medium / large-v3 (推荐) | ~5-8min | ~5GB | 中 (~15 错字, 降 75%) | 生产转录 |
| 平衡 | distil-large-v3 | ~2-3min | ~5GB | 接近 large-v3 | 速度+准确率平衡 |

**铁律**: CLI `argparse.choices` + Web API `Field(pattern)` + Web UI `<option>` 三处必须同步。

### 4.5.4 转录流程 (抖音视频)

1. **下载**: `DouyinDownloader` (Playwright 浏览器自动化, 规避 yt_dlp cookies 限制)
2. **转录**: faster-whisper `large-v3` (CUDA int8) + `initial_prompt="以下是简体中文的句子。"` + VAD 过滤
3. **后处理**: LLM 修正专有名词/同音字/标点/分段 (待 API key 启用自动化,当前手工 LLM 后处理)

### 4.5.5 16 个抖音视频转录产出

位于 `output-test/transcripts/` 目录,文件名已统一为纯中文主题名:

```
杀戮美学.md          文学与地理.md       拉美文学.md         日本情色社会.md
民族隐喻.md          AI与水资源.md       地理决定论.md       00后被辜负.md
俄国推理小说.md      武侠诞生.md         包青天vs福尔摩斯.md 山水发明.md
白马文学史.md        中国画敬畏.md       布料与位置.md       迷恋江湖.md
```

每个文件包含: `<!-- post-process: llm-reviewed -->` banner + 标题 + 元信息 + 详情题记 (blockquote) + 正文。

### 4.5.6 抖音 DASH 格式踩坑 (E1)

- **现象**: 批量下载的 mp4 (50-80MB) `container.streams.audio[0]` 报 `tuple index out of range`
- **根因**: 抖音使用 DASH 格式, 视频流和音频流分离。大文件是 video-only, 小文件 (10-15MB) 才含音频
- **解决方案**: `smart_retry_failed.py` 实现智能下载策略 — 捕获所有 douyinvod URL → HEAD 探测大小 → PyAV probe 音频流 (下载前 256KB 用 av.open 检测) → 选最小含音频文件下载

---

## 五、GPU 加速

### 5.1 当前环境

- **GPU**: NVIDIA GeForce RTX 4060 Laptop (8GB VRAM)
- **PyTorch**: 2.6.0+cu124
- **CUDA**: 12.4
- **device 自动解析**: `cuda`

### 5.2 模型缓存位置

| 引擎 | 位置 | 已有模型 |
|------|------|---------|
| openai-whisper | `C:\Users\12739\.cache\whisper\` | base, small, medium, large-v3 |
| faster-whisper | `C:\Users\12739\.cache\huggingface\hub\` | 首次使用时下载 |
| 项目缓存 | `C:\Users\12739\AppData\Local\video2text\Cache\` | learned_terms.json |

### 5.3 使用方式

```powershell
# 自动检测 GPU
python -m video2text transcribe "video.mp4" --model large-v3

# 强制指定设备
$env:VIDEO2TEXT_DEVICE = "cuda"

# 使用 faster-whisper (需先下载模型)
$env:VIDEO2TEXT_ENGINE = "faster-whisper"
$env:HF_ENDPOINT = "https://hf-mirror.com"  # 国内镜像
```

### 5.4 性能对比

| 模型 | CPU | CUDA (RTX 4060) | 加速比 |
|------|-----|-----------------|--------|
| small | ~5 min | ~1 min | 5x |
| medium | ~15 min | ~2 min | 7.5x |
| large-v3 | ~30 min | ~4 min | 7.5x |

---

## 六、测试

### 6.1 测试结构

```
douyin_batch/tests/           # 751 测试
├── test_learn.py             # 32 tests (ASR 学习)
├── test_pipeline_async.py    # 10 tests (异步管道)
├── test_pipeline_stages.py   # 34 tests (Stage 链)
├── test_post_process.py      # 24 tests (后处理)
├── test_web_app.py           # Web 服务
├── test_web_app_rate_limit.py # 速率限制
├── test_web_app_security.py  # 安全
├── test_mcp_server.py        # MCP 工具
├── test_vad_chunking.py      # VAD 分块
├── test_persistent_cache.py  # 持久缓存
├── test_profile_cli.py       # profile CLI
├── test_e2e_real_urls.py     # 真实 URL E2E (7 skipped, 需网络)
└── ... (共 48+ 个测试文件)
```

### 6.2 运行测试

```powershell
# 全量测试
python -m pytest --no-header -q

# 单个模块
python -m pytest douyin_batch/tests/test_learn.py -v

# 跳过慢测试
python -m pytest -m "not slow"

# 带覆盖率
python -m pytest --cov=video2text --cov-report=term-missing
```

### 6.3 已知 skip 的测试

14 个 skipped 测试主要是:
- `test_e2e_real_urls.py` (7 个) — 需要真实网络访问
- `test_round5_four_features.py` (4 个) — `process_single_video_safe` 已迁移到 Pipeline 层
- `test_one_click.py` (1 个) — 平台特定
- 其他 (2 个) — 环境依赖

### 6.4 测试基线 (v3.2.0d)

- **基线**: 751 passed / 14 skipped / 0 failed (2026-07-21)
- **回归判定**: 新改动后 passed 数 < 751 视为回归
- **P3 backlog 相关测试**: 不阻塞当前版本,允许 skip

### 6.5 代码风格

```powershell
# Ruff 检查
python -m ruff check .

# Ruff 格式化
python -m ruff format .
```

---

## 七、CI/CD

### 7.1 GitHub Actions 工作流

| 文件 | 用途 |
|------|------|
| `test.yml` | 矩阵测试 (Python 3.8-3.12 × Win/Mac/Linux) |
| `release.yml` | PyPI + Docker Hub + GitHub Release (OIDC) |
| `security.yml` | Bandit 安全扫描 |

### 7.2 发布流程 (release.yml)

```
push tag v*.*.*
    │
    ├─ lint (ruff)
    ├─ test (matrix: py3.8-3.12 × win/mac/linux)
    ├─ build (wheel + sdist)
    ├─ publish-pypi (OIDC trusted publisher)
    ├─ publish-docker (多架构: amd64 + arm64)
    └─ github-release (从 RELEASE_NOTES 生成)
```

### 7.3 发布前准备

**PyPI**: 需在 PyPI 网站配置 Trusted Publisher (GitHub Actions OIDC)
**Docker Hub**: 需在 GitHub Secrets 配置 `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN`

```powershell
# 发布步骤
git tag v3.2.0b
git push origin v3.2.0b
# GitHub Actions 自动触发
```

---

## 八、关键配置

### 8.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIDEO2TEXT_DEVICE` | `auto` | 设备: auto/cpu/cuda/metal |
| `VIDEO2TEXT_ENGINE` | `whisper` | 引擎: whisper/faster-whisper/whisperx |
| `VIDEO2TEXT_MAX_WORKERS` | `min(cpu_count, 4)` | 异步并发数 |
| `VIDEO2TEXT_VRAM_PER_TASK_MB` | `3000` | GPU 显存感知并发: 每任务预估显存 (MB),`_gpu_aware_concurrency` 按 `free // vram_per_task` 收紧并发数 |
| `VIDEO2TEXT_RATE_LIMIT` | `10` | Web 速率限制 (req/60s), `0` 禁用 |
| `VIDEO2TEXT_CACHE_DIR` | XDG 默认 | 缓存目录 |
| `VIDEO2TEXT_WECHAT_COOKIE` | — | 微信公众号 cookies |
| `HF_ENDPOINT` | `https://hf-mirror.com` | HuggingFace 国内镜像 |
| `HF_TOKEN` | — | WhisperX pyannote 令牌 |

### 8.2 启动方式

```powershell
# CLI
python -m video2text transcribe "video.mp4"
python -m video2text transcribe "https://www.douyin.com/video/xxx"
python -m video2text batch v1.mp4 v2.mp4

# Web 服务
python main.py --mode web
# 访问 http://localhost:8000

# Docker
docker compose up -d

# 一键启动
make dev   # 或
just dev
```

### 8.3 速率限制

- 默认 10 请求/60秒/IP
- 超限返回 `HTTP 429` + `Retry-After` + `X-RateLimit-*` headers
- `/api/health` 暴露当前限制配置
- `VIDEO2TEXT_RATE_LIMIT=0` 完全禁用

---

## 九、注意事项与已知问题

### 9.1 Python 3.8 兼容性

所有使用 PEP 604/585 语法的文件必须 `from __future__ import annotations`。

### 9.2 Python 路径 (Windows)

使用 `C:\Users\12739\AppData\Local\Programs\Python\Python312\python.exe`。Windows Store 的 `python` 是 stub,不可用。

### 9.3 PowerShell 语法

- 用 `;` 不是 `&&`
- `Set-Location` 不是 `cd /d`
- `& "abs\path.exe"` 调用带引号路径
- `Select-Object -Last N` 替代 `tail`

### 9.4 抖音下载依赖

```powershell
# 首次使用需安装 Playwright 浏览器
python -m playwright install chromium
```

### 9.5 torch 2.6 兼容

`transcribers/whisper.py` 模块级自动补丁修复 `torch.load`权重加载问题。

### 9.6 faster-whisper 国内网络

首次使用 `faster-whisper` 需从 HuggingFace 下载模型。`config.py` 自动配置 `HF_ENDPOINT=https://hf-mirror.com`。如仍超时,建议:
1. 手动下载模型到 `~/.cache/huggingface/hub/`
2. 或使用 `whisper` 引擎 (已有本地模型)

### 9.7 TRAE Sandbox 限制 (非阻塞)

- TRAE 自带 ffmpeg 精简版不支持 wav muxer,faster-whisper 用 PyAV 直接解码 mp4 跳过音频提取
- Playwright headless 写 debug.log 被 sandbox 拦 (stderr 报 EPIPE,忽略不影响主流程)
- 其他文件访问限制 (WeType xlog / nvAppTimestamps / debug.log): 非阻塞错误

### 9.8 抖音 DASH 格式

抖音使用 DASH 格式,视频流和音频流分离。批量下载的 video-only mp4 (50-80MB) 没有音频流。需用智能下载策略:捕获所有 douyinvod URL → HEAD 探测大小 → PyAV probe 音频流 → 选最小含音频文件下载。

### 9.9 归档目录

`archive/` 目录包含开发过程中的遗留脚本、临时数据、旧测试,已加入 `.gitignore`。需要查看历史实现时可参考。

### 9.10 并发安全

`learn.py` 的 read-modify-write 模式在多进程并发时可能丢数据。如需高并发场景,考虑用 sqlite 替代 JSON。

### 9.11 转录文本状态声明

必须显式声明转录文本状态。未启用 LLM 时加 `<!-- post-process: llm-disabled -->` 标签,启用时加 `<!-- post-process: llm-reviewed -->`。避免用户误解为已审查。

---

## 十、后续路线图

### v3.3.0 (近期)

1. **LLM 真实 API 联调** — 需用户提供 API key,启用自动化 LLM 后处理
2. **LLM 流式输出** — 长文本 (30s+) 等待问题,按时间戳分段独立调用 LLM
3. **Web UI 后处理状态标签** — 解析 md 头部 banner 显示状态
4. **默认引擎切换** — 根据 benchmark 结果决定是否默认 `faster-whisper`
5. **CLI dispatcher 全局选项修复** — `--workspace` 报 unknown command 问题

### v3.4.0 (中期)

1. **分布式 pipeline** — 多机转录
2. **WhisperX 共识模式** — 多引擎投票提升准确率
3. **按时间戳分段独立调用 LLM** — 长文本流式后处理

### 长期

1. **实时流式转录** — 边录边转
2. **多语言 UI** — 扩展 i18n 支持更多语言
3. **插件市场** — 第三方下载器/转录引擎插件

---

## 十一、快速上手清单

新接手者请按以下顺序操作:

- [ ] 1. `git clone` + `pip install -e ".[dev]"`
- [ ] 2. `python -m playwright install chromium` (抖音下载依赖)
- [ ] 3. `python -m pytest --no-header -q` 确认 751 测试全绿
- [ ] 4. `python -m video2text transcribe "test.mp4"` 跑一个本地文件
- [ ] 5. `python main.py --mode web` 启动 Web 服务,访问 `localhost:8000`
- [ ] 6. 阅读 [pipeline_stages.py](file:///d:/1/video2text/video2text/pipeline_stages.py) 理解 Stage 链
- [ ] 7. 阅读 [learn.py](file:///d:/1/video2text/video2text/learn.py) 理解 ASR 自动学习
- [ ] 8. 阅读 [llm_post_process.py](file:///d:/1/video2text/video2text/llm_post_process.py) 理解 LLM 后处理骨架 (v3.2.0d)
- [ ] 9. 检查 `git status` 了解未提交改动 (v3.2.0b/c/d 全量未 commit,用户指示)
- [ ] 10. 浏览 `output-test/transcripts/` 下 16 个转录 md 文件了解产出格式
- [ ] 11. 先与用户确认是否 commit,再开始新功能开发

---

## 十二、关键文件索引

| 文件 | 用途 |
|------|------|
| [pipeline_stages.py](file:///d:/1/video2text/video2text/pipeline_stages.py) | Stage 链核心 |
| [pipeline.py](file:///d:/1/video2text/video2text/pipeline.py) | Pipeline + GPU 解析 |
| [pipeline_async.py](file:///d:/1/video2text/video2text/pipeline_async.py) | 异步 Pipeline |
| [learn.py](file:///d:/1/video2text/video2text/learn.py) | ASR 自动学习 |
| [post_process.py](file:///d:/1/video2text/video2text/post_process.py) | 术语校正 + Prompt |
| [llm_post_process.py](file:///d:/1/video2text/video2text/llm_post_process.py) | LLM 后处理工程化骨架 (v3.2.0d) |
| [config.py](file:///d:/1/video2text/video2text/config.py) | 配置 (含 10 模型扩展) |
| [downloaders/douyin.py](file:///d:/1/video2text/video2text/downloaders/douyin.py) | 抖音 Playwright |
| [web/app.py](file:///d:/1/video2text/video2text/web/app.py) | Web 服务 |
| [__main__.py](file:///d:/1/video2text/video2text/__main__.py) | CLI 入口 |
| [output-test/transcripts/](file:///d:/1/video2text/output-test/transcripts) | 16 个转录 md 产出 |
| [.github/workflows/release.yml](file:///d:/1/video2text/.github/workflows/release.yml) | 发布 CI/CD |

---

*文档更新时间: 2026-07-21 · 测试: 751 passed, 14 skipped, 0 failed · 当前版本: v3.2.0d (未提交)*
