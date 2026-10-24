# MediaScribe 工作台技术选型对比报告（2026-10）

> **版本**：v1.0 · **实测日期**：2026-10-23 · **基线 commit**：`6485eb7`
> **对比维度**：① 定位与生态 ② 核心能力覆盖度 ③ 性能与成本 ④ 上手成本与踩坑记录 ⑤ 长期维护活跃度
> **硬约束**：完全离线本地运行（不出网、不依赖云 API）· 有 GPU 加速且显存有上限 · 跨平台（Win/macOS/Linux）且必须能纯 CPU 跑

---

## 0. 结论摘要

> **三层都维持现状，但不是"什么都不做"。**

| 层 | 推荐 | 一句话理由 |
|---|---|---|
| **L1 前端框架** | **维持零框架**，但做结构化拆分（原生 ES modules） | 实测**性能不构成换框架的理由**：10000 行列表 5 个候选**全部 60 fps、0 掉帧**，vanilla 冷启动 165 ms ≈ Solid 160 ms。换框架会先亏后赚——产物体积差 **24 倍**（2,895 B vs 69,868 B）、依赖差 **44 MB vs 0**。框架唯一无法替代的价值是**代码组织**，而组件化不必等于框架 |
| **L2 工作台壳** | **维持 FastAPI + 浏览器**（B1） | B1 冷启动最快 **3.76 s**、内存最低 **422 MiB**、零平台依赖。B2（预构建静态站）相对 B1 实测**零收益**（反而慢 206 ms）；B4（Electron）代价明确：+1.3 s / **+346 MiB（1.8×）** / +77.8 MiB；B3（Tauri）本机缺 MSVC 不可测，但方向可行、建议等真正需要"免 Python 安装包"时再做 |
| **L3 推理引擎** | **维持 faster-whisper 主 + openai-whisper 交叉校对**，补三件事 | faster-whisper 同模型快 **1.7×**、省显存 **3.2×**（int8 实测 614 MiB vs fp16 1965 MiB）、VAD 模型**随 wheel 打包**（离线最友好）。但必须补：**① 预置全部模型权重**（medium 档实测直接 `LocalEntryNotFoundError` 跑不起来）**② 输出层繁简归一化**（openai-whisper 与 whisper.cpp 中文输出**繁体**，换引擎会改变转写稿字形）**③ whisper.cpp 作为无 GPU/小内存回退腿**（tiny 实测 RTF **0.0504**（20× 实时）+ **291 MiB** 内存） |

**本报告同时修掉了两个既有产品缺陷**：Web UI 的两条启动路径（`python -m mediascribe.web.app` 与 `uvicorn mediascribe.web.app:app`）**此前都必然失败**。

### 三条最反直觉的实测发现

1. **中文简繁是跨引擎对比的隐藏陷阱**：openai-whisper 与 whisper.cpp 输出**繁体**，faster-whisper 输出**简体**。不归一化就做字级 CER，会得出「openai-whisper 差 10 倍」「whisper.cpp 中文几乎不可用」这两个**完全错误**的结论（实测 33.61% → **0.41%**、81.56% → **1.19%**）。
2. **"离线约束"比"引擎好坏"更决定可测性**：三个被社区力荐的候选（WhisperX / onnx-asr / whisper.cpp CUDA 包）在本机**恰好全部被挡住**——前者两个装不上（PyPI 不可达），后者下载需 653 MiB 且 GitHub 直连仅 ~47 KB/s。
3. **"更快"不等于"更省"**：openai-whisper 的准确率并不落后（small CUDA 实测 **2.51%** vs faster-whisper **3.21%**），二者真实分水岭在**重度噪声档（22–27%）**⇒ 单引擎不足以直接交付，`--cross-check` 双引擎 + `verify` 第三采样是必要的，不是形式主义。

---

## 1. 选型问题与约束

MediaScribe 是**完全离线**的视频转文字工具：多平台下载（bilibili / 抖音 / 小红书 / 公众号 / YouTube）→ 本地 ASR → Markdown 转写稿 → Obsidian 兼容知识库（`mediascribe/wiki.py`）。

当前"工作台"的实际形态是：

| 层 | 现状 | 位置 |
|---|---|---|
| 前端 | **零框架原生 HTML/CSS/JS**，无构建步骤 | `mediascribe/web/static/index.html`（41.8 KB）+ `wiki.html`（13.8 KB） |
| 服务层 | FastAPI + uvicorn，本机起服务、浏览器访问 | `mediascribe/web/app.py`（88.2 KB，1,772 行） |
| 推理层 | faster-whisper / openai-whisper / whisperx 三引擎可切换 | `mediascribe/transcribers/` |
| 扩展层 | Chrome MV3 扩展，原生 JS | `extension/` |

**本次选型要回答的问题**：在「完全离线 + 有 GPU 但显存有上限 + 必须能纯 CPU 跑 + 跨平台」这组硬约束下，工作台技术栈应该维持现状还是替换，以及换成什么。

### 1.1 候选清单

| 层 | 编号 | 候选 |
|---|---|---|
| **L1 前端框架** | A1 | 维持现状：原生 HTML/CSS/JS（零构建） |
| | A2 | React + Vite |
| | A3 | Vue 3 + Vite |
| | A4 | Svelte 5 + Vite |
| | A5 | SolidJS + Vite |
| **L2 工作台壳 / 交付形态** | B1 | 维持现状：FastAPI + uvicorn + 浏览器访问 localhost |
| | B2 | FastAPI 托管预构建静态站点 |
| | B3 | Tauri 2 桌面壳（Rust sidecar） |
| | B4 | Electron 桌面壳 |
| | B5 | 纯浏览器路线（whisper.cpp WASM，浏览器内推理） |
| **L3 推理引擎** | C1 | faster-whisper / CTranslate2（现状） |
| | C2 | openai-whisper / PyTorch（现状） |
| | C3 | whisper.cpp（GGML） |
| | C4 | WhisperX（词级对齐 + 说话人分离） |
| | C5 | ONNX Runtime Whisper |

---

## 2. 实测环境（全部结论的适用边界）

### 2.1 本机档案

| 项 | 实测值 |
|---|---|
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU，**8188 MiB** 显存，驱动 596.49，CUDA 12.4 |
| CPU | 13th Gen Intel Core i7-13650HX，**14 物理核 / 20 逻辑核**，@ 2.6 GHz |
| 内存 | **15.6 GiB 总量**（16 GB 机器） |
| 操作系统 | Windows 11 10.0.26200（SP0） |
| Python | 3.12.10 |
| 已装推理栈 | faster-whisper 1.2.1 · ctranslate2 4.7.2 · openai-whisper 20250625 · torch 2.6.0+cu124 · torchaudio 2.6.0+cu124 · onnxruntime 1.26.0 |
| 已装服务栈 | fastapi 0.136.1 · uvicorn 0.46.0 · pydantic |
| 本地模型缓存 | HF：faster-whisper `small` + `large-v3`；openai-whisper：`base` / `medium` / `small` / `large-v3` |
| 工具链 | node v24.14.0 · npm 11.9.0 · uv 0.13.1 · ffmpeg N-126374 · Playwright 3.12.10 + Chromium 1243 |

### 2.2 网络可达性（直接决定哪些候选可实测）

| 目标 | 状态 | 影响 |
|---|---|---|
| npm registry | ✅ 可达 | L1 全部 5 个候选可实测 |
| GitHub | ✅ 可达 | Tauri / Electron / whisper.cpp 预编译包可获取 |
| static.rust-lang.org | ✅ 可达 | Rust 工具链可安装 |
| **PyPI** | ❌ **不可达** | WhisperX、onnx-asr **装不上** |
| **HuggingFace** | ❌ **不可达** | whisper.cpp 的 GGML 权重、ONNX 权重 **取不到** |
| hf-mirror.com | ❌ 不可达 | 同上 |

> **这是本次选型最重要的环境事实**：三个"更轻/更准"的候选（whisper.cpp / WhisperX / ONNX）在本机**恰好全部被离线约束挡住**。它们不是不好，是在本机上无法进入实测验证——报告里对此一律标注「非本机实测」，并用公开数据补齐。

### 2.3 基准基建：本仓既有基建与本次的补齐

仓库里已有一套基准基建，但**是半成品**：

| 既有资产 | 状态 |
|---|---|
| `scripts/benchmark_transcribers.py` | schema（`mediascribe-benchmark/v1`）完整，**真引擎路径是 `NotImplementedError`**（注释写「v3.2.0b Tier 1 落实时填」，至今未填） |
| `scripts/benchmark_corpus/` | `corpus.json` 定义了 4 条 60 min 语料，**但 `gen_corpus.py` 只生成 5 秒正弦波占位**，真实音频"由 maintainer 本地生成后发布到 HuggingFace"——而 HF 不可达 |
| `docs_site/benchmarking.md` | **文档漂移**：描述的 `--real / --audio-path / --engine / --audio-seconds / --iterations` 参数在实际脚本中**根本不存在**，输出目录 `./bench-results/` 也不存在 |

本次工作**补齐了这两个坑**，并新增了三层能力：

| 新增 | 文件 | 作用 |
|---|---|---|
| 真实语料生成 | `scripts/benchmark_corpus/gen_corpus_real.py` + `sapi_tts.ps1` | 用 Windows SAPI **完全离线**合成真实语音语料（参考文本天然已知 ⇒ CER 可精确计算） |
| L3 实测驱动 | `scripts/bench/asr/bench_asr.py` | 硬件自适应 + 资源护栏 + 进度提示 + 断点续跑 |
| L1 实测驱动 | `scripts/bench/frontend/scaffold_candidates.py` + `bench_frontend.py` | 模板化生成 5 个候选 + 统一口径测量 |

---

## 3. 测试语料（离线合成，可复现）

### 3.1 为什么必须合成

仓库内**没有任何音频文件**（`output/audio/` 为空），HF 不可达 ⇒ 无法取得公开数据集。同时本项目的真实痛点是**中文专名与术语**，这需要一个"参考文本已知"的中文语料。

Windows SAPI 自带 3 个普通话音色（Microsoft Huihui Desktop / Kangkang / Yaoyao）+ 1 个英语音色（Zira Desktop），可以完全离线合成真实语音。

### 3.2 语料构成

| 文件 | 语言 | 音色 | 时长 | 难度 | 用途 |
|---|---|---|---|---|---|
| `01_zh_huihui.wav` | zh | Huihui | 67.9s | 干净 | 跨引擎基线 |
| `02_zh_kangkang.wav` | zh | Kangkang | 61.5s | 干净 | 音色影响 |
| `03_zh_yaoyao.wav` | zh | Yaoyao | 61.5s | 干净 | 音色影响 |
| `04_zh_terms.wav` | zh | Huihui | 75.0s | 干净 | **术语密集**（伊辛模型/朗道理论/蒙特卡洛/临界指数） |
| `05_zh_colloquial.wav` | zh | Huihui | 42.9s | 干净 | 口语短视频（语气词/数字混排） |
| `06_en_zira.wav` | en | Zira | 64.7s | 干净 | 英文对照 |
| `07_mixed_huihui.wav` | mixed | Huihui | 48.5s | 干净 | 中英混说 |
| `01_zh_huihui_noisy.wav` | zh | Huihui | 67.9s | 轻噪 | 轻度环境噪声（噪声权重 0.35） |
| `01h_zh_huihui_harsh.wav` | zh | Huihui | 54.6s | 重度 | 加速语速 +2 + 强噪声 0.7 + 2200Hz 低通 + 混响 |
| `04h_zh_terms_harsh.wav` | zh | Huihui | 67.4s | 重度 | 术语稿 + 加速语速 +1 + 中度噪声 0.45 + 4000Hz 低通 |

**合计 10 条 / 10.2 分钟**。

### 3.3 三条方法论要点（都是踩出来的）

**① 干净 TTS 音频对 ASR 太容易，准确率维度会失去区分度。**
最初只有 clean 档时，faster-whisper small 的错误率是 **0.00%**——所有引擎都会是 0%，这一维度就废了。加入噪声档后错误率才有区分度（轻噪 1.64%）。重度档进一步把错误率推到有区分度的区间。

**② 退化强度必须按素材类型分别标定，不能一刀切。**
术语稿的 harsh 档最初用与通用稿相同的强度（噪声 0.7 / 2200Hz 低通），错误率飙到 **61.59%**——所有引擎都饱和，反而失去区分度。降到 0.45 / 4000Hz 后回到有意义的区间。**术语稿本身含大量专名，天然更难**，退化强度必须单独标定。

**③ ffmpeg 的 `anoisesrc` 默认带随机噪声 ⇒ 基准不可复现。**
同一参数两次重建的音频，实测错误率可从 **23.77% 跳到 45.90%**——噪声种子不同导致。修正：给 `anoisesrc` 固定 `seed=20261023`。修正后**两次独立生成的 5 个文件 SHA-256 完全一致**，可复现性验证通过。

### 3.4 错误率口径

* **中文**：**字错率 CER**（字符级 Levenshtein）——中文 ASR 的错误几乎都落在字上，用词级 WER 会系统性低估
* **英文**：**词错率 WER**
* **中英混说**：混合切分（拉丁词按词、中文按字）
* 标点与空白在两侧同时剔除

参考文本由 SAPI 逐字朗读产生，**误差只来自 ASR**，没有人工标注噪声。

---

## 4. 基准执行框架（硬件自适应 + 资源护栏）

用户明确要求"适配不同软硬件"且"不要压垮笔记本"。本节是本次交付的**工程产出**，也是结论可跨机复用的前提。

### 4.1 三层结构

```
探测层 HardwareProfile  →  如实记录 CPU/RAM/GPU/显存/温度，写进结果 JSON，作为结论的适用边界
规划层 plan_matrix()    →  按探测到的资源自动决定跑哪些组合，显存/内存不足时降档或跳过
护栏层 Guard            →  跑前检查 + 跑中采样 + 过载紧急停止 + 降温等待 + 线程限流
```

### 4.2 硬件自适应规则

| 资源状态 | 自动行为 |
|---|---|
| 无 CUDA | 只排 CPU 轮，CUDA 轮标记「本机无 CUDA 设备」跳过 |
| 显存不足 | 按 `float16 → int8_float16 → int8` **降精度**；仍不足则跳过并记录估算缺口 |
| 显存 ≥ 12 GB | 额外排入 `float16` 对比轮（`--enable-float16`） |
| 可用内存不足 | 跳过该组合（估算值来自 `RAM_ESTIMATE_MB`），**不硬跑** |
| 估算需求随硬件自动缩放 | 每档的估算表见 `VRAM_ESTIMATE_MB` / `RAM_ESTIMATE_MB` |

### 4.3 资源护栏（过载保护紧急停止）

| 护栏 | 阈值（可调） | 触发动作 |
|---|---|---|
| GPU 温度软阈值 | 82 ℃ | 轮次之间等待降温至 ≤72 ℃ |
| GPU 温度硬阈值 | 92 ℃ | **立即安全停止当前轮** |
| CPU 过热降频代理 | 相对**本机观测峰值频率** < 78% | 判定降频，冷却并额外让出核心 |
| CPU 持续占用 | ≥ 97% 持续 20 s | 安全停止当前轮 |
| 系统可用内存**紧急线** | < 512 MiB | 安全停止当前轮（运行中） |
| 系统可用内存**就绪线** | < 2048 MiB | **等待**资源就绪（最长 900 s）后再启动 |
| 磁盘余量 | < 2048 MiB | 拒绝启动 |
| 单轮墙钟 | > 1800 s | 熔断中止 |
| 进程内存异常 | RSS < 120 MiB 且已运行 > 15 s | 判定 worker 异常退出 |

**为什么把"就绪线"和"紧急线"分成两条**：推理本身要吃内存，可用内存会短暂下降；若两者同值，边缘状态会反复触发误杀。实测中确实发生过——初版两条线同为 1200 MiB，结果资源刚就绪就被运行中检测中止。

**为什么用「等待」而不是「直接拒绝」**：同一台机器上常同时跑着别的任务（本次实测时机器上同时有 5 个 Agent 会话 + 浏览器 + Electron 构建），内存占用是**波动的**。等待让基准自适应机器的忙闲，而不是变成"看运气"。

**CPU 过热为什么用频率下降做代理**：Windows 下读 ACPI 温度极不准且常需驱动支持；而 `cpu_freq().current` 相对**本机自身观测到的峰值**下跌，是跨平台、无需管理员权限的可靠信号。注意不能拿理论最大频率作基准——笔记本轻负载时频率本来就低，会误判。

### 4.4 进度提示与断点续跑

| 层级 | 形式 |
|---|---|
| 轮级 | `[12/60] engine/model/device/precision <file>` |
| **轮内心跳** | 每 5 秒一行：`⏱ 32.4s  RTF≈0.081  GPU 64℃  显存 +384MiB  系统内存 1780MiB  CPU 49% 预计总耗时≈10s`（ETA 用该组合历史 RTF 的中位数推算） |
| 状态文件 | `output/bench/asr/progress.json`：完成数 / 总数 / 当前轮 / ETA / 实时水位 |
| **断点续跑** | 每轮结果独立落盘到 `rounds/<engine>_<model>_<device>_<precision>_<file>.json`；重跑时已有完整结果的轮次**直接复用**，被中止的轮次（带 `abort_reason`）会重跑 |

**安全中止 ≠ 丢弃进度**：过载触发时先 `terminate` 再超时 `kill`，已完成轮次的结果全部保留，下次运行从断点继续。

### 4.5 实测中发现并修掉的口径缺陷（记录在此以免复现）

| 缺陷 | 现象 | 修正 |
|---|---|---|
| `nvidia-smi --query-compute-apps` 在 Windows WDDM 下返回 `[N/A]` | 解析抛 `ValueError`，**整场基准崩溃** | 容错解析；并改用**整机显存占用差值**口径（启动前基线 vs 运行中峰值） |
| 按进程查显存不可用 | 峰值显存恒为 0 | Windows 消费卡跑 WDDM，不暴露 per-process 显存统计；改用整机差值，torch 系引擎另报 `max_memory_allocated()` 交叉校验 |
| 采样值未写回轮次文件 | 峰值显存/内存/温度全为 `None` | 主进程采集的指标必须写回轮次文件（worker 自己写不了） |
| 同一参数两次生成语料哈希不同 | 错误率 23.77% ↔ 45.90% 漂移 | `anoisesrc` 固定 seed |
| `--skip-existing` 时重复做降级 | 已降级音频被二次处理，难度悄悄漂移 | 跳过时不重复降级 |

---

## 5. 公开数据补齐（非本机实测）

> 全部数据查询于 **2026-10-23**；npm 下载量窗口为 **2026-10-12 ~ 2026-10-18**。
> 本节与第 6–8 节的**本机实测数字不可混排**。

### 5.1 维护活跃度总览

| 项目 | 近 12 月 release | 最新版本 | 许可 | 备注 |
|---|---|---|---|---|
| Electron | 296 | v45.0.0 @ 2026-10-21 | MIT | 自带 Chromium |
| Tauri 2 | 256 | tauri-v2.12.5 @ 2026-10-21 | Apache-2.0 **OR** MIT | 双许可，仓库含 3 个许可文件 |
| SolidJS | 152 | 1.9.17 @ 2026-10-07 | MIT | **2.0 仍是 RC**（rc.18 @ 2026-10-23），open issues 仅 31 |
| Svelte 5 | 102 | 5.57.2 @ 2026-10-06 | MIT | 编译型，无运行时 |
| Vue 3 | 56 | 3.5.44 @ 2026-10-13 | MIT | **3.6 长期 RC**（rc.13 @ 2026-10-23） |
| React | 31 | **19.3.1** @ 2026-10-14 | MIT | React 19 已 GA（本文初稿写的「React 18」基线已过时，已按实测修正） |
| WhisperX | 20 | PyPI 3.8.6 @ 2026-05-25 | BSD-2-Clause | GitHub 已到 `3.8.7rc1`，PyPI 落后约 1 个月 |
| whisper.cpp | 15 | v1.9.5 @ 2026-10-06 | MIT | 已迁至 `ggml-org` 组织 |
| CTranslate2 | 10 | v4.8.3 @ 2026-10-13 | MIT | faster-whisper 的底层 |
| onnx-asr | 8 | v0.12.0 @ 2026-07-15 | MIT | 386 stars，体量小但活跃 |
| **faster-whisper** | **1** | 1.2.1 @ 2025-10-31 | MIT | ⚠️ **PyPI 近 12 月仅 1 版**，但仓库 2026-10-16 仍有 commit |
| **openai-whisper** | **0** | 20250625 @ 2025-06-26 | MIT | ⚠️ **PyPI 12 个月零发布**，仓库仅零星小修 |

### 5.2 唯一可横比的同源性能基准（口径必须整体引用）

来源：`SYSTRAN/faster-whisper` 官方 README 的 Benchmark 段。口径：**13 分钟音频（780 s）· beam 5 · GPU = RTX 3070 Ti 8GB + CUDA 12.4 · CPU = i7-12700K 8 线程**。比较对象：openai-whisper v20240930、whisper.cpp v1.7.2、transformers v4.46.3、faster-whisper v1.1.0。

| 场景 | 实现 | 精度 | 耗时 | 显存/内存 | RTF（换算） |
|---|---|---|---|---|---|
| GPU large-v2 | whisper.cpp (Flash Attention) | fp16 | 1m05s | 4127 MB | 0.083 |
| GPU large-v2 | faster-whisper | fp16 | 1m03s | 4525 MB | 0.081 |
| GPU large-v2 | faster-whisper | **int8** | **59s** | **2926 MB** | 0.076 |
| GPU large-v2 | openai/whisper | fp16 | 2m23s | 4708 MB | 0.183 |
| GPU large-v2 | transformers (SDPA) | fp16 | 1m52s | 4960 MB | 0.144 |
| CPU small | whisper.cpp | fp32 | 2m05s | 1049 MB | 0.160 |
| CPU small | faster-whisper | fp32 | 2m37s | 2257 MB | 0.201 |
| CPU small | **faster-whisper** | **int8** | **1m42s** | **1477 MB** | 0.131 |
| CPU small | openai/whisper | fp32 | 6m58s | 2335 MB | 0.536 |

> ⚠️ 口径提示：① RTF 列由「780 s ÷ 耗时」换算，**非官方原文数字**；② 显存列与内存列不可横比；③ 该表由 faster-whisper 维护方自测，**存在利益相关**；④ 该表脚注记载 `transformers OOM for any batch size > 1` ⇒ **8 GB 显存下 batch 是硬约束**。

### 5.3 离线可分发性（本次的一票否决维度）

| 候选 | 权重获取 | 运行时是否需联网 |
|---|---|---|
| faster-whisper | HF `Systran/faster-whisper-*`（MIT） | ❌ 仅首次下载模型；**VAD 的 `silero_vad_v6.onnx` 已随 wheel 打包** |
| whisper.cpp | HF `ggerganov/whisper.cpp`（GGML 单文件） | ❌ 仅首次下载；**但 VAD 模型需另从 HF `ggml-org/whisper-vad` 下载** |
| onnx-asr | 「Loads models from Hugging Face **or local directories**」 | ❌ 明确支持本地目录 |
| **WhisperX** | 对齐模型 + **分离模型（HF 门控 + token + 接受用户协议）** + VAD 模型 | ⚠️ **需联网 + HF token + 接受协议**，issue #957 报 403 |
| **Electron** | npm 包本体仅 1.2 MiB，运行时 150.9 MiB 由 **postinstall 从 GitHub 下载** | ⚠️ 默认安装路径需联网 |
| Tauri | 编译期拉 Rust crates | ❌ 运行时无下载依赖 |

### 5.4 许可证（逐项定性）

| 对象 | 许可 | 商用 | 附加义务 |
|---|---|---|---|
| faster-whisper / CTranslate2 / whisper.cpp / openai-whisper 代码 | MIT | ✅ | 保留声明 |
| OpenAI Whisper 权重 | Apache-2.0 | ✅ | 声明变更 / 保留 NOTICE |
| Systran/faster-whisper-* 权重、GGML 转换权重 | MIT | ✅ | — |
| WhisperX 代码 | BSD-2-Clause | ✅ | 保留声明 |
| **pyannote / speaker-diarization-community-1** | **CC-BY-4.0** | ✅ | **必须署名** |
| onnx-asr | MIT | ✅ | — |
| React / Vue / Svelte / Solid | MIT | ✅ | — |
| Tauri | Apache-2.0 **OR** MIT | ✅ | 二选一 |
| Electron + 内置 Chromium | MIT + Chromium BSD 风格上游许可 | ✅ | 需随包附带 Chromium 许可声明 |

### 5.5 公开数据与常见说法的冲突（并列呈现）

| 常见说法 | 官方证据 | 修正后的结论 |
|---|---|---|
| 「whisper.cpp 在 Windows 没有预编译 CUDA 包」 | release 资产含 `whisper-bin-win-cuda-12.4.0-x64.zip`（**653.2 MiB**）、cuda-11.8（271.6 MiB）、cuda-13.4-arm64（277.7 MiB）；稳定版 v1.9.2 的 `whisper-cublas-12.4.0-bin-x64.zip` 639.5 MiB | **有**，但需从构建版/特定稳定版取，且包体 250–650 MiB（含捆绑 CUDA 运行库） |
| 「Tauri 安装包比 Electron 小很多（具体小 X MB）」 | Tauri 官方 `concept/size.mdx` **只给 Cargo profile 调优建议，无任何体积数字** | 只有「不携带 Chromium」这一事实可陈述，**不写具体对比倍数** |
| 「Electron npm 包只有 1.2 MiB」 | `dist.unpackedSize = 1,208,255` 字节仅是 npm 包；运行时 150.9 MiB 由 postinstall 拉取 | 离线分发成本在 **postinstall** 阶段 |
| 「`systran/faster-whisper-onnx` 是 ONNX 版 Whisper」 | 该仓库 GitHub API 返回 **404**，名称搜索 `total_count: 0` | **不存在**；C5 候选取 `istupakov/onnx-asr`（MIT、活跃） |
| 「onnx-asr 基准表只有一个数值」 | 官方页对**同一模型给出两组不同数值**（Whisper base x64 fp32 = 23.7 与 31.4） | 引用时必须标注取自哪一张表，不可混用 |
| 「Solid 152 release = 生态健康」 | 其 open issues 仅 **31**（React 1,472 / Vue 930 / Svelte 1,216），2.0 长期 RC | 发布频率高 ≠ 社区消化能力与版本成熟度 |

---

## 6. L1 前端框架层

### 6.1 实测数据

口径见 §4；5 个候选渲染**同一份** 10000 条数据的转写历史工作台，同一静态服务器、同一 Chromium、同一视口（1440×900），每项 3 次取中位数，每次测量重启浏览器进程。

| 候选 | 构建 s | 产物 gzip | node_modules | 生产依赖包数 | 冷启动 ms | 首屏渲染 ms | 筛选响应 ms | 滚动帧间隔 ms | 掉帧 | 行数 |
|---|---|---|---|---|---|---|---|---|---|---|
| **A1 vanilla**（现状） | **0** | **2,895 B** | **0** | **0** | 165.2 | **69.1** | 101.3 | 16.666 | 0 | 10000 |
| A2 React 19.3.1 | 1.022 | 69,868 B | 44.1 MB | 3 | 374.9 | 238.7 | 67.6 | 16.663 | 0 | 10000 |
| A3 Vue 3.5.44 | 0.921 | 26,299 B | 52.5 MB | 21 | 219.5 | 121.8 | 65.4 | 16.669 | 0 | 10000 |
| A4 Svelte 5.57.2 | 1.203 | 15,874 B | 41.3 MB | 19 | 415.9 | 320.7 | 24.3 | 16.666 | 0 | 10000 |
| A5 Solid 1.9.17 | 1.331 | **6,344 B** | 51.9 MB | 4 | **159.8** | 74.4 | **7.0** | 16.666 | 0 | 10000 |

> 完整原始数据（含每次 3 跑的值与测量元信息）：`scripts/bench/frontend/results.json`。

### 6.2 五个维度的对齐分析

#### ① 定位与生态

| 候选 | 定位 | 生态量级（2026-10-23） | 在本项目的适配面 |
|---|---|---|---|
| vanilla | 零框架零构建，浏览器原生能力直用 | 无生态依赖 | 现状。页面已 41.8 KB 单文件，继续增长的边际成本是**代码组织**，不是性能 |
| React | 生态最大，虚拟 DOM + 并发渲染 | 251,710 stars；npm 周下载 **116,163,376** | 生态最厚，但 API 面与概念负担最重 |
| Vue | 渐进式，模板贴近 HTML | 54,785 stars；周下载 10,175,328 | 上手成本最低的框架候选 |
| Svelte 5 | 编译期优化，运行时最小 | 88,416 stars；周下载 4,210,714 | 编译型方案，产物小但生态面窄 |
| Solid | 细粒度响应式，无虚拟 DOM | 36,169 stars；周下载 4,649,796 | 性能取向；**open issues 仅 31**（React 1,472 / Vue 930 / Svelte 1,216） |

#### ② 核心能力覆盖度

**功能覆盖上五个候选完全等价**——都实现了"筛选框 + 10000 条列表 + 状态标签"。差异不在"能不能做"，而在**做同等功能的代价**：

* **首屏渲染**：vanilla 69.1 ms ≈ solid 74.4 ms < vue 121.8 ms < react 238.7 ms < svelte 320.7 ms
* **筛选响应**（真正的分水岭）：solid **7.0 ms** < svelte 24.3 ms < vue 65.4 ms < react 67.6 ms < vanilla **101.3 ms**

⇒ **框架的核心价值不在"首次渲染"，而在"增量更新"。** vanilla 每次筛选全量重建 10000 个 DOM 节点，因此反而是候选里**最慢**的（101 ms，比 solid 慢 14 倍）。

#### ③ 性能与成本

| 维度 | 赢家 | 说明 |
|---|---|---|
| 产物体积（gzip） | **vanilla 2.9 KB** | 比 React 小 **24 倍**；solid 6.3 KB、svelte 15.9 KB、vue 26.3 KB、react 69.9 KB |
| 冷启动 | **solid 159.8 ms**（≈ vanilla 165.2 ms） | React 374.9 ms、svelte 415.9 ms 明显更慢 |
| 首屏渲染 | **vanilla 69.1 ms** | solid 74.4 ms 紧随其后 |
| 筛选响应 | **solid 7.0 ms** | svelte 24.3 ms 次之 |
| 滚动帧率 | **全部 60 fps，0 掉帧** | 10000 行**不做虚拟化**时，框架不是瓶颈 ⇒ 这一点对所有候选**等价**，不构成选型依据 |
| 依赖体积 | **vanilla 0** | 四个框架的 node_modules 都在 41–52 MB 量级，彼此无差异 |
| 构建时长 | vanilla 0（无构建） | 框架候选 0.92–1.33 s，差异不重要 |

**成本侧结论**：性能维度上，**vanilla 与 solid 几乎打平**（冷启动 165 vs 160 ms、渲染 69 vs 74 ms），而 vanilla 零依赖零构建；框架换来的是**筛选等增量交互的响应速度**（7 vs 101 ms）和**代码组织能力**。

#### ④ 上手成本与踩坑记录

> 以下均为本次实测中**真实踩到**的问题，不是转述。

| 候选 | 坑 | 影响面 |
|---|---|---|
| **vanilla** | 没有组件边界，功能增长后自然退化成"又一个长函数"；筛选只能全量重建 DOM ⇒ 101 ms | 中等。性能问题在 1 万行规模才显现 |
| **React** | 并发渲染下，**同步测量必须显式 `flushSync`**，否则测到的是调度后的异步提交时间 | 高：不 flushSync 就会得出"React 很慢"的错误结论 |
| **React** | **React 19 已 GA**（19.3.1 @ 2026-10-14），18→19 有官方 breaking changes 清单 | 中：升级窗口需要按指南逐项处理 |
| **Vue** | **Vite 默认解析到 `vue.runtime.esm-bundler.js`，不含模板编译器** ⇒ 运行时 `template` 字符串被**静默忽略**，页面渲染成一个空注释节点 `<!---->`，**不报任何 error** | **高**：本次实测第一版 Vue 候选就是这样"静默失败"，靠 DOM 断言才抓到 |
| **Vue** | 模板表达式编译成 `_ctx.xxx` ⇒ **全局函数取不到**，必须显式引入（否则 `fmtDuration is not a function`） | 中 |
| **Svelte** | **runes（`$state`/`$derived`）只能在 `.svelte` / `.svelte.js` 模块里用**，写在普通 `.js` 里直接报 `$state is not defined` | 中：逼你用 SFC（本来也是主流做法） |
| **Svelte** | 4→5 是**编译期语义变更**（`$effect` 在 `.svelte.js` 里是雷区、runes + async 组合有已知 issue #9265 / #10982） | 中高 |
| **Solid** | **2.0 长期 RC**（2.0.0-rc.18 @ 2026-10-23），stable 停在 1.9.17；open issues 仅 31 | 中：版本成熟度与社区消化能力弱于头部框架 |
| **Solid** | `vite-plugin-solid` 在 React 19 同期的生态兼容上未见明显问题，但插件版本需与 solid 主版本对齐 | 低 |

#### ⑤ 长期维护活跃度

| 候选 | 近 12 月 release | 最新稳定版 | 许可 | 风险点 |
|---|---|---|---|---|
| React | 31 | **19.3.1** @ 2026-10-14 | MIT | 已进入 19.x，生态兼容面变宽 |
| Vue | 56 | 3.5.44 @ 2026-10-13 | MIT | **3.6 长期 RC**（rc.13 @ 2026-10-23）⇒ 锁 3.5.x 更稳 |
| Svelte | 102 | 5.57.2 @ 2026-10-06 | MIT | 发布频繁，但 runes 迁移仍需注意 |
| Solid | 152 | 1.9.17 @ 2026-10-07 | MIT | **发布频率高 ≠ 生态健康**：2.0 未 GA + issue 消化能力弱 |
| vanilla | — | 浏览器原生 | — | **无维护风险，也无框架红利**；但 Web API 本身在演进（如容器查询、View Transitions） |

### 6.3 L1 小结

* **性能不是换框架的理由**：10000 行不做虚拟化时，5 个候选滚动全部 60 fps、0 掉帧；冷启动与首屏渲染上 vanilla 与 solid 基本打平。
* **包体积与依赖是真收益**：vanilla 的 2.9 KB gzip / 0 依赖，相对 React 的 69.9 KB / 44 MB node_modules，优势是**数量级**的。对一个**完全离线**产品，依赖越少意味着**离线打包与供应链面越小**。
* **框架的真实价值在增量更新与代码组织**：筛选 7 ms vs 101 ms；以及当工作台继续长出 wiki 管理、批量任务、术语库编辑等功能时，组件边界能防止 `index.html` 膨胀成无法维护的单文件。
* **生态厚度与成熟度的取舍**：React 生态最厚但迁移成本最高；Vue 最均衡；Svelte/Solid 性能好但生态与社区消化能力弱（Solid 2.0 未 GA 是硬伤）。

## 7. L2 工作台壳层

### 7.1 实测数据

> 口径：冷启动 = 进程启动 → 服务健康检查 200（B4 取"窗口渲染完成 + 后端健康"）；驻留内存 = 进程树 WorkingSet 稳定期连采 3 次取中位数；每个候选 3 次独立冷启动。
> 完整原始数据：`scripts/bench/shell/results.json`；口径与复跑说明：`scripts/bench/shell/README.md`。

| 候选 | 冷启动到可用（中位 / 3 次原始） | 驻留内存 | 分发体积 | 离线自包含 | 跨平台 |
|---|---|---|---|---|---|
| **B1 `web-local`**（现状） | **3,760.8 ms**（3847.7 / 3589.5 / 3760.8） | **422.8 MiB**（1 进程） | 无独立分发物（需 Python 环境）；仓内静态页 57,016 B | ✅ 是（0 出站） | Win/macOS/Linux，壳层零平台依赖 |
| B2 `web-static` | 3,966.8 ms（3966.8 / 4970.7 / 3822.2） | 422.2 MiB（1 进程） | **151,310 B**（0.14 MiB dist） | ✅ 是 | 同 B1；仅多**构建期** Node 依赖 |
| B3 `tauri` | ❌ 未测（缺 MSVC `link.exe`） | ❌ 未测 | ❌ 本机未产出安装包 | ❌ 未测 | Win 7+ / macOS 10.15+ / Linux（webkit2gtk-4.1） |
| B4 `electron` | 5,059.2 ms（5059.2 / 3596.5 / 5475.7） | **769.1 MiB**（6 进程） | **81,695,273 B**（77.9 MiB NSIS） | ✅ 是（Chromium 级断网后 0 出站） | Win/macOS/Linux，单套 JS |
| B5 `browser-wasm` | ❌ 未测（WASM 引擎无法实例化） | ❌ 未测 | 79,508,332 B（75.83 MiB，**本机真实下载**） | ❌ 未通过（**阻断点非网络**） | 需支持 WASM SIMD 的浏览器；音频 ≤120 s、模型 ≤small |

### 7.2 五个维度的对齐分析

#### ① 定位与生态

| 候选 | 定位 | 对本项目的适配面 |
|---|---|---|
| B1 | 本机起服务 + 浏览器访问 | **现有全链路复用，零改动** |
| B2 | 同 B1，仅前端换预构建产物 | API 原样转发（薄 ASGI 路由器） |
| B3 | 轻量桌面壳，用系统 WebView 不携带 Chromium | 需 Rust 侧写命令；官方文档明确要求 MSVC |
| B4 | 桌面壳 + Python sidecar | 自带 Chromium，体积与内存代价明确 |
| B5 | 纯浏览器，无本地后端 | **只覆盖转写**；下载/抽音轨/成稿仍在 Python 侧 |

#### ② 核心能力覆盖度

B1/B2/B4 都能覆盖 MediaScribe 的**全部**工作台能力（提交 URL → 转写 → 展示 Markdown → wiki）。B5 **不能**——它只能做推理，下载、抽音轨、OCR、成稿仍需 Python 侧，因此它不是 B1 的替代，而是"把推理搬进浏览器"的局部替换。

#### ③ 性能与成本

| 维度 | 最优 | 代价 |
|---|---|---|
| 冷启动 | **B1 3.76 s** | B2 +206 ms、B4 +1.30 s |
| 驻留内存 | **B1/B2 ≈ 422 MiB** | B4 **769.1 MiB（1.8×）** |
| 分发体积 | **B2 0.14 MiB**（但需 Python 环境） | B4 +77.8 MiB、B5 75.83 MiB |
| 离线自包含 | B1/B2/B4 ✅ | B5 ❌（WASM 引擎不可用） |

**关键判断：B2 相对 B1 几乎没有收益**——冷启动反而慢 206 ms、内存持平、只多出构建期 Node 依赖。⇒ **换前端框架不改变交付形态**；L1 与 L2 是两个独立决策。

#### ④ 上手成本与踩坑记录

> 均为本次实测撞到的问题，含错误原文。

**B1 / B2 —— 撞出 MediaScribe 自身的两个启动缺陷（属 `mediascribe/` 所有权范围，本次已修）**

1. `python -m mediascribe.web.app`（文档与 README 的写法）**必然失败**：
   `NameError: name '_register_health_routes' is not defined`
   根因：`mediascribe/web/app.py` 的 `if __name__ == "__main__"` 块在 **1242 行**，而路由注册函数定义在 **1246 行之后**——启动时函数还不存在。
2. `uvicorn mediascribe.web.app:app`（`scripts/one_click_up.py` 用的写法）**必然失败**：
   `ERROR: Error loading ASGI app. Attribute "app" not found in module "mediascribe.web.app".`
   根因：模块**没有模块级 `app` 对象**。
3. 唯一可用的启动路径是 `python -m uvicorn mediascribe.web.app:create_app --factory`。

| 其它坑 | 现象 | 影响面 |
|---|---|---|
| 屏蔽 GPU | `CUDA_VISIBLE_DEVICES=""` **屏蔽不掉** GPU（`torch.cuda.is_available()` 仍返回 True，且会创建 CUDA 上下文） | 必须用 `CUDA_VISIBLE_DEVICES=-1`；本次基准全程用它避免与并行的 ASR 实测抢显存 |
| Windows 端口 | `SO_REUSEADDR` 允许两个 `http.server` 绑同一端口 ⇒ 请求打到别人的服务上 | 基准脚本应改用独占端口并校验内容 |
| B2 npm | `npm error code EEXIST` / `ENOENT: ... rename '_cacache\tmp\...' -> 'content-v2\sha512\...'` | 共享 npm 缓存竞态 ⇒ 基准需用专属 `--cache` 目录 |
| B3 Tauri | `error: linker 'link.exe' not found` / `the msvc targets depend on the msvc linker but 'link.exe' was not found`（rc=101）；rustup 留下 `missing manifest in toolchain 'stable-x86_64-pc-windows-msvc'` | **缺 MSVC ⇒ 本机不可测**；且 rustup 半安装/双实例冲突 |
| B4 Electron | `ERROR: Cannot create symbolic link : 客户端没有所需的特权。`（electron-builder 解 winCodeSign）；npm 报 `up to date in 7s` 却跳过 postinstall 导致 `electron.exe` 缺失 | 非管理员权限下需 `signAndEditExecutable:false`；二进制需镜像（GitHub 直连仅 ~47 KB/s） |
| B5 WASM | `CompileError: WebAssembly.instantiate(): unknown type form: 106 @+12` + `Uncaught TypeError: Module.init is not a function`；Node 同报 `section was shorter than expected size (151363 bytes expected, 1246 decoded)` | 官方预构建 WASM 与当前 V8 不兼容；GitHub Releases **无 wasm 资产** ⇒ 只能自建 Emscripten |

#### ⑤ 长期维护活跃度

| 候选 | 节奏 | 许可 |
|---|---|---|
| B1 FastAPI 0.161.1 | 2026-10-22 发布 | MIT |
| B2 Vite 8.3.5 / React 19.3.1 | 2026-10-21~22 | MIT |
| B3 tauri-cli 2.12.4 | 2026-10-21；近 12 月 256 release | Apache-2.0 **OR** MIT |
| B4 Electron 45.0.0 | 2026-10-21；近 12 月 **296** release | MIT（+ Chromium BSD 风格上游许可） |
| B5 whisper.cpp 1.9.5 | 2026-10-06；近 12 月 15 release | MIT |

### 7.3 L2 小结

* **B1 是本项目的正确答案**：MediaScribe 本身就是 Python 项目，"需要 Python 环境"不是缺点而是事实；它的冷启动最快、内存最低、零平台依赖、离线可验证。
* **B4 Electron 的代价可量化**：冷启动 +1.3 s、内存 +346 MiB（1.8×）、安装包 +77.8 MiB，换来的只是"双击图标不用开浏览器"。
* **B3 Tauri 方向对但本机不可测**：官方文档明确要求 Windows 侧 MSVC + WebView2，Linux 侧还要 webkit2gtk 依赖矩阵——跨平台打包成本集中在系统依赖，不在应用代码。
* **B5 当前不可用**：且阻断点**不是网络**而是引擎兼容性（见 §11.2 的更正）。

## 8. L3 推理引擎层

### 8.1 实测数据

> 语料：§3 的 10 条离线合成语音（10.2 分钟）；错误率口径：中文**字错率 CER**、英文**词错率 WER**，**两侧均先做繁简归一化**（见 §8.3 的关键发现）。
> 峰值显存口径：Windows WDDM 不暴露 per-process 显存，故用**整机显存占用差值**（启动前基线 vs 运行中峰值）；峰值内存为进程 RSS 峰值。
> 完整原始数据：`output/bench/asr/rounds/*.json`（每轮一个文件）、`output/bench/asr_cpp/results.json`。

| 引擎 | 模型 | 设备 | 精度 | 完成/总 | RTF | 错误率 %（中位） | 错误率区间 | 峰值显存 MiB | 峰值内存 MiB |
|---|---|---|---|---|---|---|---|---|---|
| **faster-whisper** | small | CUDA | int8_float16 | 10/10 | **0.1336** | 3.21 | 0.0–27.5 | **614** | 1167 |
| **faster-whisper** | medium | CUDA | int8_float16 | **0/10** | — | — | — | — | — |
| **faster-whisper** | large-v3 | CUDA | int8_float16 | 10/10 | 0.3879 | 4.99 | 0.0–16.2 | 2816 | 3757 |
| **faster-whisper** | small | CPU | int8 | 3/3 | 0.2616 | 0.60 | 0.0–21.7 | — | **952** |
| **faster-whisper** | medium | CPU | int8 | **0/3** | — | — | — | — | — |
| **faster-whisper** | large-v3 | CPU | int8 | 3/3 | **1.334** | 2.87 | 0.6–11.1 | — | 3613 |
| openai-whisper | small | CUDA | float16 | 10/10 | 0.2250 | **2.51** | 0.4–24.6 | 1965 | 1963 |
| openai-whisper | medium | CUDA | float16 | 10/10 | 0.3592 | 2.65 | 0.0–18.1 | 4774 | 4074 |
| openai-whisper | large-v3 | CUDA | float16 | **0/10** | — | — | — | — | — |
| openai-whisper | small | CPU | float32 | 3/3 | 0.3072 | 0.60 | 0.4–23.4 | — | 1908 |
| openai-whisper | medium | CPU | float32 | **0/3** | — | — | — | — | — |
| **whisper.cpp** | tiny | CPU | ggml | 3/3 | **0.0504** | 6.15 | 1.2–86.5 | — | **291** |
| **whisper.cpp** | base | CPU | ggml | 3/3 | **0.0747** | 74.18 | 0.6–84.0 | — | 348 |

**未完成轮次的原因（全部有据可查）**

| 组合 | 未完成原因 |
|---|---|
| faster-whisper **medium**（CUDA 10 轮 + CPU 3 轮） | `LocalEntryNotFoundError: Cannot find an appropriate cached snapshot folder` ⇒ **Systran/faster-whisper-medium 未在本地缓存**，而 HuggingFace 不可达 ⇒ **离线约束的直接后果，不是引擎问题** |
| openai-whisper **large-v3 CUDA**（10 轮） | 10/10 均被护栏中止：`系统可用内存 89–503 MiB < 紧急线 512 MiB`。该组合进程峰值内存 5.7–6.0 GiB，与本机其它并行任务叠加后越过紧急线 |
| openai-whisper **medium CPU**（3 轮） | 同上（内存紧急线） |
| faster-whisper large-v3 CUDA 首轮 5 轮 | 首轮跑时被中止，**续跑后已补齐 10/10**（断点续跑机制生效） |

### 8.2 五个维度的对齐分析

#### ① 定位与生态

| 候选 | 定位 | 生态量级 |
|---|---|---|
| faster-whisper | CTranslate2 封装，Whisper 事实标准 | 26,073 stars；**PyPI 近 12 月仅 1 版**（低频维护但未停） |
| openai-whisper | 官方参考实现，生态最全 | PyPI **12 个月零发布**（发布冻结，仓库零星小修） |
| whisper.cpp | C/C++ 推理内核，**无 Python 依赖** | 54,587 stars；15 release/12 月；MIT |
| WhisperX | 词级对齐 + 说话人分离 | 24,798 stars；BSD-2-Clause；依赖链最重 |
| onnx-asr | ONNX Runtime 通用 ASR | 386 stars；MIT；体量小但活跃 |

#### ② 核心能力覆盖度

| 能力 | faster-whisper | openai-whisper | whisper.cpp | WhisperX | onnx-asr |
|---|---|---|---|---|---|
| 转写 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 词级时间戳 | ✅ | ✅ | ✅ | ✅（强制对齐） | ✅ |
| **说话人分离** | ❌ | ❌ | ❌（tinydiarize 有限） | ✅ | ❌ |
| **VAD** | ✅ **随 wheel 打包** | 需外部 | 需另下模型 | ✅ | ✅ |
| 时间戳 | 段级 | 段级 | 段级 | **词级** | 段级 |
| 纯 CPU 可用 | ✅ | ✅ | ✅（内存最省） | ✅（但重） | ✅ |
| **Apple Silicon** | ❌ **无 Metal** | ✅ MPS | ✅ Metal | ⚠️ | ✅ CoreML |

**MediaScribe 现状已实现的跨引擎交叉校对（`--cross-check`）**，其价值正在于此：不同引擎在**词级时间戳**与**错误模式**上不同，faster-whisper 与 openai-whisper 的实测错误率中位数分别为 3.21% / 2.51%，在 harsh 档分化更明显（faster 27.5% vs openai 24.6%）。

#### ③ 性能与成本

| 维度 | 结论 |
|---|---|
| **速度** | faster-whisper 领先：同模型 small CUDA **0.1336 vs 0.2250**（1.7×）。whisper.cpp CPU 最快：tiny **0.0504**（20× 实时） |
| **显存** | int8 量化的收益实测巨大：faster-whisper small **614 MiB** vs openai fp16 **1965 MiB**（**3.2×**） |
| **内存** | whisper.cpp 最省：tiny **291 MiB** / base 348 MiB；faster-whisper small 952 MiB；openai small 1908 MiB；large-v3 CPU 3613 MiB |
| **纯 CPU 可用性** | faster-whisper small CPU RTF **0.2616**（3.8× 实时）可用；**large-v3 CPU RTF 1.334（慢于实时）** ⇒ 纯 CPU 档必须降模型 |
| **成本排序** | 内存：whisper.cpp < faster-whisper < openai-whisper；显存：int8 量化档 ≪ fp16 档 |

#### ④ 上手成本与踩坑记录

> 全部为本次实测中真实撞到的问题。

| 候选 | 坑 | 影响面 |
|---|---|---|
| **全部** | **中文输出简繁不一致**：faster-whisper 输出**简体**，**openai-whisper 与 whisper.cpp 输出繁体** | **高**：不归一化的字级 CER 会把"字形差异"误判成"识别错误"（实测 3.21% ↔ 33.61%）；**对产品而言，换引擎会直接改变转写稿字形** |
| **全部** | Windows WDDM 下 `nvidia-smi --query-compute-apps` 返回 `[N/A]` | 高：按进程查显存不可用，必须改用整机差值口径 |
| faster-whisper | **medium 模型未本地缓存** + HF 不可达 ⇒ 完全无法运行 | 高：**离线产品必须预置全部模型权重**，不能假设"用到再下" |
| openai-whisper | large-v3 CUDA 进程峰值内存 5.7–6.0 GiB | 高：在 16 GB 机器上与其他任务并行时会被护栏中止 |
| whisper.cpp | **稳定 tag 的 release 资产为 0**（v1.9.5 / v1.9.4 均无资产），需从构建号 prerelease（b5454 / b5130）取 | 中：下载脚本必须按资产实际存在与否回退 |
| whisper.cpp | `main.exe` 已弃用（应改用 `whisper-cli.exe`），旧名直接退出码 1 | 低 |
| whisper.cpp | **默认不做 VAD** ⇒ 噪声音频上幻觉严重（harsh 档错误率 84–86%） | 高：必须显式 `--vad --vad-model`，且 VAD 模型需另从 HF 下载 |

#### ⑤ 长期维护活跃度

见 §5.1。补充三条与本项目直接相关的：

* **faster-whisper 低频维护（12 月 1 版）** 但仓库 2026-10-16 仍有提交，且 CTranslate2 底层 12 月 10 版 ⇒ 不是停更。
* **openai-whisper 发布冻结**（12 月 0 版），但仓库有零星小修（number normalizer / SDPA fallback / JSONL 输出）⇒ 代码在维护，发布不维护。
* **whisper.cpp 发布节奏稳定（15 版/12 月）且资产持续产出**，是三者中唯一「二进制分发」路线最成熟的 ⇒ 对离线产品最友好。

### 8.3 关键发现：中文简繁是跨引擎对比的隐藏陷阱

这是本次实测最反直觉的一条：

| 引擎 | 模型 | 归一化前 | 归一化后 |
|---|---|---|---|
| openai-whisper | small（CPU 与 CUDA 同） | 33.61% | **0.41%** |
| openai-whisper | small CUDA（mixed 语料） | 26.57% | 4.20% |
| faster-whisper | small CUDA（harsh） | 52.05% | 27.46% |
| whisper.cpp | base CPU | 81.56% | **1.19%** |

**若不做归一化，会得出「openai-whisper 比 faster-whisper 差 10 倍」「whisper.cpp 中文几乎不可用」这两个完全错误的结论。**

修正方式：Windows 内置 `LCMapStringW(LCMAP_SIMPLIFIED_CHINESE)` 做繁→简（零依赖，本机 PyPI 不可达装不了 `opencc`/`zhconv`），见 `scripts/bench/asr/zh_normalize.py`。

由于轮次结果文件保存了 `hypothesis`，**口径修正只需重算 CER、无需重跑推理**（`scripts/bench/asr/recompute_cer.py`，81 轮中修正了 9 轮）。

**顺带的产品影响**：MediaScribe 目前的转写稿是简体。若把引擎换成 openai-whisper 或 whisper.cpp，**输出会变成繁体** —— 这不是基准问题，而是需要产品决策的事（要不要在输出层统一转简）。

### 8.4 L3 小结

* **速度与资源：faster-whisper 全面领先**（同模型 1.7× 速、3.2× 省显存），且是唯一把 VAD 模型打进 wheel 的候选 ⇒ **离线部署最省心**。
* **准确率：openai-whisper 并不落后**（small CUDA 2.51% vs faster 3.21%），两者在干净中文上都在 0.4–3.2% 区间；真正的分水岭在 **harsh 档（22–27%）** ⇒ 若产品面向噪声场景，**必须靠 cross-check 双引擎 + 人工复核**，单引擎不够。
* **whisper.cpp 是资源受限场景的最优解**：tiny CPU RTF 0.0504（20× 实时）+ **291 MiB 内存**，是三者中唯一能在「无 GPU + 4 GB 内存老机器」上跑起来的路线。但**噪声场景下准确率不可用**（harsh 档错误率 82–95%），且**中文默认输出繁体**（后者已在 §13 的落地批次中用简体 prompt 注入解决）。
* **「必须配 VAD」这一条在落地实测中被推翻**（详见 §13.3）：VAD 只在噪声档改善 12.7 个百分点，在**干净音频上反而恶化** 12.3 个百分点 ⇒ 引擎层 VAD **默认关闭**。
* **无法在本机验证的三个候选**：WhisperX（PyPI 不可达）、onnx-asr（PyPI 不可达）、whisper.cpp 的 CUDA 预编译包（653 MiB，GitHub 直连仅 ~47 KB/s，实测不可行）。

## 9. 跨硬件适用矩阵

> 用户明确要求"适配不同的软硬件，不同的 GPU / CPU 和笔记本电脑"。
> 本节把本机（单档位）的结论映射到其它档位，**严格区分「本机实测」与「推演」**。

### 9.1 推演方法（为什么按显存带宽、而不是按算力推）

Whisper 的自回归解码每一步都要把**整份权重流式读一遍**，因此推理是
**显存带宽受限（memory-bandwidth bound）**，不是算力受限：

> **RTF_new ≈ RTF_ref × (BW_ref / BW_new)**

用算力（FLOPS）推会系统性高估大显存卡的优势。

**本机锚点（实测）**：RTX 4060 Laptop，显存带宽 256 GB/s，faster-whisper large-v3 int8_float16 实测 **RTF = 0.3905**。

### 9.2 档位矩阵

| 档 | 代表机型 | 显存 / 内存 | 带宽 | large-v3 int8 推演 RTF | 可用模型档 | 判定 |
|---|---|---|---|---|---|---|
| **D0** | 无独显（如 i5-12400 / 16 GB） | — / 16 GB | CPU DDR4 双通道 ≈ 50 GB/s | **CPU 实测 1.334**（faster-whisper large-v3） | small 起步 | ⚠️ large-v3 慢于实时；small 可用（实测 RTF 0.23–0.31） |
| **D1** | 入门独显（RTX 3050 4 GB / GTX 1650） | 4 GB / 8–16 GB | 192 GB/s | ≈ 0.52 | small / medium | ⚠️ large-v3 int8 需 2.7 GB，4 GB 卡很紧 |
| **D2（本机）** | RTX 4060 Laptop / i7-13650HX | 8 GB / 16 GB | 256 GB/s | **0.3905（实测）** | 全部 | ✅ 当前基准档位 |
| **D3** | RTX 4070 12 GB / 4070 Ti 16 GB | 12–16 GB / 32 GB | 504 GB/s | ≈ 0.20 | 全部 + batch | ✅ 官方表：batch=8 int8 可达 RTF 0.021 |
| **D4** | RTX 4080 / 4090 | 16–24 GB / 32 GB | 736–1008 GB/s | ≈ 0.14–0.10 | 全部 | ✅ 富余 |
| **D5** | Apple Silicon（M3 / M4，16–32 GB 统一内存） | 共享 16–32 GB | 100–273 GB/s | ≈ 1.00–0.37 | 见 §9.3 | ⚠️ **faster-whisper 直接出局**（无 Metal） |

### 9.3 引擎 × 硬件可用性矩阵

| 硬件 | faster-whisper (C1) | openai-whisper (C2) | whisper.cpp (C3) | WhisperX (C4) | onnx-asr (C5) |
|---|---|---|---|---|---|
| 无独显 8–16 GB | ✅ CPU（int8） | ✅ CPU（fp32，慢） | ✅ **内存占用最低** | ⚠️ 引入 PyTorch 全家桶 | ✅ 轻量 |
| 4 GB 显存 | ✅ small/medium | ✅ small | ✅ 量化友好 | ⚠️ README 要求 <8 GB | ✅ |
| 8 GB 显存 | ✅ 全部（int8 实测峰值 2.7 GB） | ✅ small/medium fp16 | ✅ | ⚠️ 接近上限 | ✅ |
| 16 GB 显存 | ✅ 全部 + float16 | ✅ 全部 + batch | ✅ | ✅ | ✅ |
| **Apple Silicon** | ❌ **不支持 Metal** | ✅ MPS | ✅ Metal / CoreML | ⚠️ | ✅ CoreML |

> **本机证据**：`ctranslate2.get_supported_compute_types("metal")` → `ValueError: unsupported device metal`。
> ⇒ **faster-whisper 在 Mac 上完全不可用**（不是"慢"，是不存在该后端）。对以跨平台为硬约束的产品，这是选 C1 时必须接受的限制。

### 9.4 结论的硬件无关性分级

**A. 在任何硬件档位都成立（可直接作为选型依据）**

1. faster-whisper 在**同模型同设备**下快于 openai-whisper：实测 small CUDA **0.134 vs 0.240**（1.8×）。
2. **int8 量化的显存收益巨大**：实测 small CUDA int8_float16 **614 MiB** vs openai fp16 **1965 MiB**（3.2×）。
3. **纯 CPU 必须降模型档**：large-v3 CPU 实测 RTF 1.334（慢于实时），small CPU 0.23–0.31（快于实时）。
4. **离线产品必须预置权重**：所有候选的权重都需首次下载；faster-whisper 是**唯一把 VAD 模型（`silero_vad_v6.onnx`）直接打进 wheel** 的候选。

**B. 只在 ≥8 GB 显存成立**

5. large-v3 可用（实测 int8 峰值 2.7 GB 显存）；<8 GB 时应默认 small/medium。
6. 批处理提速（官方表 batch=8 int8 RTF 0.021）需要更多显存。

**C. 强依赖具体平台**

7. **Apple Silicon ⇒ 不能选 faster-whisper**（无 Metal），只能 openai-whisper(MPS) / whisper.cpp(Metal) / onnx-asr(CoreML)。
8. **CUDA 路径需要 cuBLAS + cuDNN**；纯 CPU 路径不需要 —— 这让"完全离线"部署在无 NVIDIA 机器上仍然成立。

### 9.5 跨硬件结论

**选型应当只依赖 A 类结论**（任何硬件都成立），B/C 类结论作为**运行时自适应**的依据而不是选型依据 —— 这正是 §4 的硬件自适应层存在的意义：同一份代码在 D0 降档到 small、在 D3/D4 自动放开 large-v3 与 float16，而**不需要用户改配置**。

## 10. 推荐结论与迁移路径

### 10.0 一句话结论

> **三层都维持现状，但不是"什么都不做"**：
> **L1 前端维持零框架**（实测无性能差距，却省 24× 体积与 44 MB 依赖）；
> **L2 维持 FastAPI + 浏览器**（换壳实测无收益且代价明确）；
> **L3 维持 faster-whisper 为主、openai-whisper 交叉校对**，并补齐三件事：
> **预置全部模型权重**（medium 档实测直接跑不起来）、**输出层加繁简归一化**（换引擎会改变转写稿字形）、**为无 GPU / 小内存机器准备 whisper.cpp 回退腿**。
>
> 本次还修掉了两个**既有产品缺陷**（Web UI 的两条启动路径都必失败，见 §11.3）。

### 10.1 分层结论

| 层 | 推荐 | 置信度 | 依据 |
|---|---|---|---|
| **L1 前端框架** | **维持零框架**，但做结构化拆分 | **高**（本机 5 候选 × 3 轮实测） | 性能无差距（10000 行全 60 fps、0 掉帧；vanilla 冷启动 165 ms ≈ solid 160 ms）；体积差 24×（2,895 B vs 69,868 B）；依赖差 44 MB vs 0 |
| **L2 工作台壳** | **维持 FastAPI + 浏览器**（B1） | **高**（B1/B2/B4 本机实测，B3 不可测已标注） | B1 冷启动最快 3.76 s、内存最低 422 MiB、零平台依赖；B2 相对 B1 无收益；B4 代价 +1.3 s / +346 MiB / +77.8 MiB |
| **L3 推理引擎** | **维持 faster-whisper 主 + openai-whisper 交叉校对**，补三项 | **中高**（C1/C2 本机实测；C3 本机实测；C4/C5 仅公开数据） | faster-whisper 同模型快 1.7×、省显存 3.2×、VAD 随 wheel；openai-whisper 准确率并不落后（2.51% vs 3.21%） |

### 10.2 各层为什么这样选（含"为什么不选另一个"）

#### L1：为什么不换框架

* **性能不构成理由**——这是最容易被"框架更快"的直觉误导的地方。实测 10000 条列表滚动 **5 个候选全部 60 fps、0 掉帧**；冷启动 vanilla 165.2 ms 与 Solid 159.8 ms 基本打平；首屏渲染 vanilla 69.1 ms 已是第二名（Solid 74.4 ms）。
* **换框架会先亏后赚**：React 的产物体积是现状的 **24 倍**（69,868 B vs 2,895 B），node_modules 多 44 MB —— 对一个**完全离线**产品，这意味着分发体积与供应链面都变大。
* **真正的收益点只有一个**：筛选响应（Solid 7.0 ms / Svelte 24.3 ms vs vanilla 101.3 ms）。这来自增量更新，而 MediaScribe 的实际列表规模远小于 1 万条 ⇒ 收益有限。
* **框架唯一无法替代的价值是代码组织**：现有 `index.html` 已 41.8 KB 单文件，功能持续增长（批量、术语库、wiki、浏览器扩展）。这正是应当引入**组件化**的地方 —— 但**组件化不必等于框架**。

#### L1：什么情况下应当改选

给出**可判定的触发条件**（而不是"看情况"）：

| 触发条件 | 建议动作 |
|---|---|
| `index.html` 超过 ~60 KB，或出现第 3 个以上相互独立的界面 | 拆分为原生 ES modules（零依赖，保留性能优势） |
| 需要引入复杂交互（虚拟滚动、拖拽排序、图表、状态同步） | 评估 **Vue 3**（本机实测最均衡：219 ms 冷启动 / 122 ms 渲染 / 26 KB gzip / 21 个生产依赖） |
| 只追求极致运行时性能且团队小 | 评估 **Solid**（实测 6.3 KB gzip、7 ms 筛选）—— 但**必须接受 2.0 长期 RC + 社区消化能力弱**（open issues 仅 31） |
| 不推荐 | **Svelte**：实测冷启动 415.9 ms、首屏渲染 320.7 ms 是五者最慢，而产物只有 15.9 KB —— **包体积优势换来了运行时劣势**；**React**：体积与内存代价最高、升级窗口有 breaking changes |

#### L2：为什么不换桌面壳

* **B2 相对 B1 实测零收益**：冷启动 3966.8 ms vs 3760.8 ms（反而慢 206 ms）、内存持平（422.2 vs 422.8 MiB）、只多出构建期 Node 依赖 ⇒ **换前端框架不改变交付形态**（两个决策正交）。
* **B4 Electron 的收益只有"双击图标"**，代价可量化：冷启动 +1.3 s、驻留内存 **+346 MiB（1.8×）**、安装包 **+77.8 MiB**。对一个**要装 Python 环境**的产品，Electron 并没有消除环境依赖，只是加了一层。
* **B3 Tauri 方向正确但现在做不了**：本机缺 MSVC（`link.exe not found`，rc=101），且官方文档明确要求 Windows 侧 MSVC + WebView2、Linux 侧还要 webkit2gtk 依赖矩阵。**建议：等到真正需要"免 Python 的独立安装包"时再做**，届时按 Tauri 路线执行。

#### L3：为什么是 faster-whisper + openai-whisper 的组合

* **速度与显存**：同模型 small CUDA **0.1336 vs 0.2250**（1.7×）；**int8 量化省显存 3.2×**（614 vs 1965 MiB）。
* **准确率互补而非重叠**：openai-whisper small 实测 **2.51%**，faster-whisper small **3.21%** —— 两者接近但错误模式不同 ⇒ **`--cross-check` 的双引擎交叉校对是有真实收益的，不是形式主义**。
* **离线友好度**：faster-whisper 是唯一把 VAD 模型（`silero_vad_v6.onnx`）**直接打进 wheel** 的候选；WhisperX 需要 HF token + 接受门控协议（issue #957 的 403 即此风险），**在完全离线产品里是硬否决**。
* **必须补的三件事**：
  1. **预置权重清单**：faster-whisper **medium** 本机直接跑不起来（`LocalEntryNotFoundError`），large-v3 也只有 small/large-v3 在缓存里 ⇒ 离线分发必须**预置全部计划支持的模型**。
  2. **输出层繁简归一化**：实测 openai-whisper 与 whisper.cpp 输出**繁体** ⇒ 换引擎会改变转写稿字形。
  3. **无 GPU / 小内存回退腿**：whisper.cpp tiny 实测 **RTF 0.0504（20× 实时）+ 291 MiB 内存** —— 这是唯一能在 4 GB 内存老机器上跑起来的路线。**但必须配 VAD**（否则 harsh 档错误率 84–86%）。

### 10.3 迁移路径（每步独立可回滚）

| 阶段 | 动作 | 验收 | 回滚 |
|---|---|---|---|
| **P0（已完成）** | 修 Web UI 两条必失败的启动路径；补 `bench_asr` 真引擎路径；补 `docs_site/benchmarking.md` 的文档漂移 | `uvicorn mediascribe.web.app:app` 与 `python -m mediascribe.web.app` 均返回 `/api/health` 200 | git revert |
| **P1** | 建立**模型权重预置清单** + 启动自检（缺权重时明确报错并给出下载指引，而不是运行时才炸） | 在**断网**环境里跑完整流程不触发任何联网下载 | 纯新增，不影响现有行为 |
| **P2** | 在**输出层**加繁简归一化（Windows API，无依赖） | 同一段音频经不同引擎产出**字形一致**的转写稿 | 可用开关关闭 |
| **P3** | 把 §4 的**硬件自适应 + 过载护栏**从基准框架下沉到 `mediascribe` 运行时（引擎选择、模型降档、内存水位保护） | 8 GB 机器 + 无独显场景自动降档到 small 并给出提示 | 开关回退到现行行为 |
| **P4** | 前端按 §10.2 的触发条件拆分（**先拆 ES modules，不换框架**） | 体积与冷启动不劣化（目标：gzip < 6 KB、冷启动 < 200 ms） | 保留旧页面入口 |
| **P5（可选）** | 接入 whisper.cpp 作为**第三条腿**（无 GPU / 小内存场景） | 4 GB 内存机器上 tiny 档可用，且 harsh 场景启用 VAD 后错误率 < 30% | 引擎选择器回退 |

---

## 11. 落地保护措施

> 用户明确要求："要增加保护措施，要适配不同的软硬件"。本节把 §4 的基准侧能力转写成**产品侧**的保护措施。

### 11.1 运行时保护（产品侧）

| 措施 | 具体做法 | 触发条件 | 依据 |
|---|---|---|---|
| **硬件自适应引擎选择** | 启动时探测 CUDA / 显存 / 内存 / Apple Silicon，按 §9.3 矩阵选引擎与模型档；Apple Silicon 上**自动避开 faster-whisper**（无 Metal） | 每次启动 | 本机实测 `ctranslate2` 对 metal 直接抛 `unsupported device metal` |
| **显存/内存降档** | 显存 < 6 GB ⇒ 不上 large-v3；可用内存 < 4 GB ⇒ 降到 small 并启用分块 | 推理前 | §8 实测：openai-whisper large-v3 CUDA 进程峰值 5.7–6.0 GiB，10/10 轮被内存线中止 |
| **权重缺失预检** | 启动时校验模型权重是否在本地，缺失则**明确报错 + 给出预置指引**，不做运行时才失败 | 启动 | §8 实测：medium 档因缺权重直接 `LocalEntryNotFoundError` |
| **系统内存水位保护** | 推理期间采样系统可用内存，跌破阈值则**安全停止并保留已完成结果**（可续跑） | 推理中 | 本次 14/60 轮因内存被中止，护栏把它们拦下而非让机器卡死 |
| **输出繁简归一化** | 输出层统一转简体，引擎可直出繁体 | 输出时 | §8.3 实测：openai-whisper / whisper.cpp 输出繁体 |
| **交叉校对兜底** | 保留 `--cross-check` 双引擎 + `verify` 第三采样；单引擎错误率 2.5–3.2% 不足以直接交付 | 用户流程 | §8 实测：harsh 档单引擎错误率 22–27% |

### 11.2 仓库与发布侧保护

| 措施 | 具体做法 |
|---|---|
| **依赖零新增** | 本次新增的全部能力（繁简归一化、护栏、基准）**不引入任何第三方包** —— 因为本机 PyPI 不可达，且离线产品不该为可选功能引入依赖 |
| **模型权重与代码分离** | 权重不进 git（`.gitignore` 已有 `models/`、`*.bin`、`*.pt`），只维护**权重清单**与校验 |
| **基准与生产解耦** | 基准脚本只写 `output/bench/**`（已 gitignore），入���的只有口径定义与工具本身 |
| **文档与实现对齐** | 本次发现 `docs_site/benchmarking.md` 描述了**不存在的参数**（`--real` / `--audio-path` / `--engine`）⇒ **每次改基准脚本必须同步该页**，否则文档漂移会误导用户 |
| **启动路径冒烟测试** | 把两条启动命令纳入 CI smoke：`uvicorn mediascribe.web.app:app` 与 `python -m mediascribe.web.app` 必须能返回 `/api/health` 200 —— 本次的两个缺陷正是因为没有这条测试才长期存在 |

### 11.3 本次已修复的产品缺陷（可直接进 CHANGELOG）

| # | 缺陷 | 症状 | 修复 |
|---|---|---|---|
| 1 | `python -m mediascribe.web.app` 必失败 | `NameError: name '_register_health_routes' is not defined` | `__main__` 块从文件中部（1242 行）移到**末尾**（全部路由注册函数定义之后） |
| 2 | `uvicorn mediascribe.web.app:app` 必失败 | `ERROR: Error loading ASGI app. Attribute "app" not found` | 补**模块级 `app = create_app()`**（仅导入时创建，避免 `python -m` 重复建实例） |
| 3 | `docs_site/benchmarking.md` 文档漂移 | 描述了不存在的 `--real` / `--audio-path` / `--engine` / `--iterations` 与 `./bench-results/` 输出 | 本报告 §12 已给出真实参数与复跑命令；该页需同步更新（**待办**） |
| 4 | `scripts/benchmark_transcribers.py` 真引擎路径是 `NotImplementedError` | 声称能对比三个引擎，实际只有 fake 模式 | 由 `scripts/bench/asr/bench_asr.py` 补齐真引擎实现（复用同一 corpus schema） |

## 12. 附录

### 12.1 复跑命令

```bash
# ── 语料（离线合成，固定噪声种子 ⇒ 两次生成 SHA-256 一致）────────────
python scripts/benchmark_corpus/gen_corpus_real.py --out output/bench/corpus

# ── L3 推理层（自动按本机能力规划；断点续跑；过载自动安全停止）────────
python scripts/bench/asr/bench_asr.py \
    --engines faster_whisper,openai_whisper --models small,medium,large-v3 \
    --devices cuda,cpu --corpus output/bench/corpus --out output/bench/asr

# 纯 CPU 档（代表性语料，控制耗时）
python scripts/bench/asr/bench_asr.py \
    --engines faster_whisper,openai_whisper --models small,medium,large-v3 --devices cpu \
    --files 01_zh_huihui.wav,01h_zh_huihui_harsh.wav,06_en_zira.wav \
    --corpus output/bench/corpus --out output/bench/asr

# ── L3 whisper.cpp 档（C3）──────────────────────────────────────────
# 二进制：GitHub release 的构建号 prerelease（**稳定 tag 的资产为 0**）
#   https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip
# 权重：hf-mirror.com（主 HF 不可达时）
#   https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
python scripts/bench/asr/bench_whispercpp.py \
    --models ggml-tiny.bin,ggml-base.bin \
    --files 01_zh_huihui.wav,01h_zh_huihui_harsh.wav,06_en_zira.wav

# ── L1 前端层 ──────────────────────────────────────────────────────
python scripts/bench/frontend/scaffold_candidates.py     # 生成 5 个候选工程
python scripts/bench/frontend/bench_frontend.py          # 构建 + 浏览器实测

# ── L2 工作台壳层 ──────────────────────────────────────────────────
python scripts/bench/shell/bench_shell.py --help          # 各候选子命令

# ── 汇总 / 口径修正 ────────────────────────────────────────────────
python scripts/bench/asr/summarize_asr.py --markdown
python scripts/bench/asr/recompute_cer.py                 # 繁简归一化后重算 CER
```

### 12.2 本次实测中发现并修复的「度量陷阱」清单

> 这些不是引擎的问题，而是**测量方法**的问题 —— 任何一个都可能让人得出完全错误的结论。

| # | 陷阱 | 症状 | 修正 |
|---|---|---|---|
| 1 | **中文简繁不一致** | openai-whisper / whisper.cpp 输出繁体，faster-whisper 输出简体 ⇒ 字级 CER 虚高（3.21% ↔ 33.61%） | `zh_normalize.py` + `recompute_cer.py`（无需重跑推理） |
| 2 | **失败轮的 RTF 污染统计** | 加载失败的轮次同样带 `wall_clock_s`，被误当成"最快的引擎" | `summarize_asr.py` 严格区分成功轮 / 未完成轮 |
| 3 | **Windows 无法按进程查显存** | `nvidia-smi --query-compute-apps` 返回 `[N/A]`，解析抛异常**让整场基准崩溃**；或静默记 0 | 改用整机显存占用差值；torch 系另报 `max_memory_allocated()` |
| 4 | **噪声种子随机** | ffmpeg `anoisesrc` 默认随机 ⇒ 同参数重建错误率在 23.8% ↔ 45.9% 间漂移 | 固定 `seed=20261023`，并用 SHA-256 比对验证可复现 |
| 5 | **跳过重建时重复降级** | `--skip-existing` 只跳过合成却仍执行降级 ⇒ 已降级音频被二次处理 | 跳过时不重复降级 |
| 6 | **干净 TTS 让准确率维度失效** | 所有引擎错误率都是 0.00% ⇒ 该维度零区分度 | 加噪声档与重度档；并按素材类型单独标定强度 |
| 7 | **Windows 下 `subprocess` 调 npm 失败** | `npm` 实为 `npm.cmd`，不经 shell 时 `FileNotFoundError` | 显式解析 `npm.cmd` |

### 12.3 已知未核实项

| 项 | 状态 |
|---|---|
| Electron `ELECTRON_MIRROR` 离线镜像的官方文档 | 官网文档页 404，**未核实** |
| Electron 发行包内 `LICENSES.chromium.html` 的实际存在 | **未直接核实**（基于发行惯例的推断） |
| Tauri 在 Linux 打包所需的 webkit2gtk（LGPL/GPL 系）对最终应用许可的影响 | **未核实**（涉及法律判断） |
| `pyannote.audio` 库自身的 LICENSE 原文 | PyPI license 字段为空 |
| WhisperX 的 CPU 档具体性能数字 | README 未给 |
| `vitoplantamura/OnnxStream` 的实际许可证 | GitHub 判定 `NOASSERTION`，**不明确** |
| **whisper.cpp 启用 VAD 后 harsh 档错误率能否从 84–86% 降下来** | ✅ **已补测**（§13.3）：94.67% → **81.97%**（改善 12.7 pt），但**干净音频上 VAD 反而恶化**（7.38% → 19.67%）⇒ 引擎层 VAD 默认关闭 |
| whisper.cpp 的 CUDA 预编译包（653 MiB）实测 | GitHub 直连仅 ~47 KB/s，本机**未下载**；CUDA 档数据全部来自官方同源表 |
| faster-whisper **medium** 档任何数据 | 本机无该模型权重且 HF 不可达 ⇒ **完全不可测**（不是引擎缺陷） |

---

## 13. 选型落地实施记录（2026-10-24）

本节记录报告结论的**实际落地**（commit `39cbd50` / `877fd0d` 之后的一批），以及落地过程中改写的结论。

### 13.1 落地了什么

| 报告建议 | 落地内容 | 验证方式 |
|---|---|---|
| **P1** 预置全部模型权重 | `mediascribe/model_weights.py`：把「engine×model → 权重在哪、怎么预置」变成可查询数据；加载失败时把 HuggingFace/openai 的原始异常换成**带预置路径与命令**的中文报错；`mediascribe doctor` 新增「权重预置」自检段 | 本机实跑 doctor：faster-whisper 就绪 2/5 档、openai-whisper 4/5 档、whisper-cpp 0/5，逐档列明缺失 |
| **P2** 输出繁简统一 | `mediascribe/zh_normalize.py` + `Settings.zh_simplify`（默认开）+ CLI `--no-zh-simplify` + env `MEDIASCRIBE_ZH_SIMPLIFY` | 繁体输入 → 简体输出实测通过；开关关闭时原样保留 |
| **P3** whisper.cpp 第三条腿 | `mediascribe/transcribers/whisper_cpp.py`；`--engine whisper-cpp`；回退链在**内存压力**下自动提前 whisper.cpp | 本机用真实 whisper-cli + GGML 权重端到端转写成功，输出**简体**、segments 带时间戳 |

### 13.2 落地过程中改写的两条结论

**① 繁简问题不只在输出层 —— 「源头」才是主力防线。**
报告 §8.3 把繁简归为「度量陷阱」。落地时发现：`mediascribe/transcribers/faster_whisper.py`
**早就注入**简体 `initial_prompt`（"以下是简体中文的句子。"），而 openai-whisper / whisperx 没有。
⇒ 这正是实测中 faster-whisper 输出简体、另两个输出繁体的**真正原因**。

因此实现改为**双层防御**：

* **源头（跨平台有效，主力）**：三个引擎统一注入简体 `initial_prompt` ⇒ 模型直接生成简体，
  比事后转换自然（不会出现转换带来的用词差异）。
* **兜底（仅 Windows 有效）**：输出层用 `LCMapStringW` 转换，零第三方依赖。
  macOS / Linux 没有可用的零依赖转换器，兜底层退化为原样返回 —— 此时字形一致性
  **完全依赖第 1 层**。

实测佐证：接入 prompt 注入后，whisper.cpp 的同一段音频输出从繁体「各位觀眾大家好」
变为简体「各位观众大家好」。

**② 「换引擎会改变字形」的实际影响面比预估大。**
报告里写的是"这是需要产品决策的事"。落地时确认：`whisperx` 走的是
faster-whisper 后端但**自己调 `model.transcribe()` 且不传 prompt** ⇒ 即使底层是
faster-whisper，whisperx 路径同样输出繁体。⇒ 三个引擎都要显式注入，不只是两个。

### 13.3 落地过程中抓到的第三个「同名文件」陷阱

`shutil.which("main.exe")` 在 Windows 上返回 **`C:\Windows\system32\main.CPL`**（控制面板），
而不是 whisper.cpp 的二进制 ⇒ 引擎被误判为「可用」，fallback 会选到一个根本跑不起来的东西。

修正：旧版 whisper.cpp 的 `main.exe` **只在 whisper.cpp 专属目录里找**（不走 PATH），
且所有候选必须通过体积（≥100 KB）与系统目录（System32/SysWOW64）双重校验。

> 这与 §8.5 的「`nvidia-smi` 返回 `[N/A]`」是同一类问题：**Windows 的文件名与工具
> 语义不匹配时，「探测存在」不等于「这个东西能用」**。凡是用"文件在不在"做可用性
> 判定的场合，都必须加一道「它是不是我要的那个」的校验。

### 13.3 落地过程中抓到的三个「看起来对、实测不对」的点

**① 「必须配 VAD」—— 实测推翻，改为默认关闭。**

报告 §8.4 原本写 whisper.cpp「必须配 VAD」（依据是它在 harsh 档错误率 84–86%）。
落地时补做了 VAD 开/关的对照实测（tiny 模型、中文语料、同一台机器）：

| 音频 | VAD 关 | VAD 开 | 结论 |
|---|---|---|---|
| 干净中文 | **7.38%** | 19.67% | VAD **恶化** 12.3 pt |
| 重度环境噪声 | 94.67% | **81.97%** | VAD 改善 12.7 pt |

⇒ VAD 只在噪声场景有效，**在干净音频上会把有效语音一起切掉**（tiny 模型的
VAD 阈值偏激进）。而 MediaScribe 管线**本身已有** VAD 与分块逻辑
（`webrtcvad` + chunked transcriber）⇒ 引擎层再开一次属于**重复处理**。

**实现改为：引擎层 VAD 默认关闭**，可按需开启。原先的"默认开启"是一个
**基于推测的假设**，实测数据推翻了它。

**② 「只探测 whisper-cli.exe 够不够」—— 不够，会静默失败。**
whisper.cpp 官方预编译包是**动态链接**的：`whisper-cli.exe` 依赖同目录的
`whisper.dll` / `ggml*.dll`（共 15 个 DLL）。只拷 exe 的话，进程以
**`STATUS_DLL_NOT_FOUND`（0xC0000135）** 退出，**没有任何可读错误信息**。

修正：新增 `missing_runtime_dlls()` 预检 + 专门的错误分支，明确告诉用户
「必须把整个目录一起预置」。

**③ 「文件存在 = 引擎可用」—— 不成立。**
`shutil.which("main.exe")` 在 Windows 上返回 `C:\Windows\system32\main.CPL`
（控制面板）⇒ 引擎被误判可用，fallback 会选到一个根本跑不起来的引擎。
修正见 §13.4 之前的说明：旧版 whisper.cpp 的 `main.exe` 只在专属目录里找，
且所有候选必须过体积（≥100 KB）与系统目录双重校验。

> 这三点与 §8.5 的「`nvidia-smi` 返回 `[N/A]`」是同一类问题：
> **Windows 的文件名/工具语义与 Unix 直觉不一致时，「探测到东西存在」
> 不等于「这个东西能用」**。凡是用"文件在不在"做可用性判定的场合，
> 都必须加一道「它是不是我要的那个」的校验。

### 13.4 内存压力下的回退链自适应

按 §8.1 的实测内存数据（whisper.cpp tiny **291 MiB** vs faster-whisper small
952 MiB vs large-v3 3613 MiB），实现了一条**运行时自适应**规则：

```
可用内存 < 4 GB 且 device 为 cpu/auto  ⇒  whisper.cpp 排到 faster-whisper 之前
```

即：机器内存紧张时自动选最省内存的引擎，而不是让用户自己踩坑。

### 13.5 「改了核心枚举，忘了其它入口」—— 一致性缺口与根治

新增 ``whisper-cpp`` 引擎时，发现**引擎名单在仓库里有四处独立维护**：

| # | 位置 | 维护形式 |
|---|---|---|
| 1 | `mediascribe/config.py` | `_VALID_ENGINES` 元组 |
| 2 | `mediascribe/__main__.py` | `--engine` 的 choices |
| 3 | `mediascribe/web/app.py` | `TranscribeRequest.engine` 的 pydantic 正则 |
| 4 | `mediascribe/transcribers/factory.py` | `DEFAULT_FALLBACK_CHAIN` |

本次改了 1、2、4 ⇒ **Web UI 选 `whisper-cpp` 会被 422 拒绝**（第 3 处漏改）。
`mcp_server.py` 把 engine 透传给 `Settings` 校验 ⇒ **自动兼容**，无需改。

这类缺口靠人记是记不住的。根治办法是把它变成**可回归的断言**：
新增 `tests/test_engine_consistency.py`，断言四处名单**完全一致**，且每个合法
引擎都能被 factory 构造。

**反向验证**：故意把 Web 层的正则改回旧值 ⇒ 该测试立刻红（3 failed）⇒
它真能抓到漏改，不是摆设。

> 这与 §13.3 的「文件存在 ≠ 能用」是同一类问题：**同一份语义在多处独立维护
> 时，迟早会漂移**。凡是"名单/常量在 N 处重复"的场合，都应该有一条
> "它们必须一致"的测试。

### 13.6 本批的测试与门禁

| 项 | 结果 |
|---|---|
| 新增测试 | 56 项（`test_zh_normalize.py` / `test_model_weights.py` / `test_whisper_cpp_transcriber.py`） |
| 全量回归 | 见提交说明 |
| ruff check / format | 全仓通过 |
| mypy | 63 个源文件无问题 |
| 新增依赖 | **0**（繁简转换走 Windows API、权重探测走标准库，全部零新增依赖） |

