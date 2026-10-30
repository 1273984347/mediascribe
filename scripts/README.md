# scripts/ — 开发与运维脚本

按**职责**分层，不是按时间堆积。所有命令都从**仓库根目录**执行。

> 2026-10-24 全量整理：原先 53 个脚本平铺在本目录，现按职责归入
> `bench/ · e2e/ · pipeline/ · tools/ · launch/ · gates/` 六个子目录。

## 目录速览

| 目录 | 职责 | 入口 |
|---|---|---|
| `bench/` | 基准测试（前端框架 / 工作台壳 / ASR 引擎 / 语料） | `bench/benchmark_transcribers.py` |
| `e2e/` | 真实网络 E2E、录制采集、CLI help 快照 | `e2e/capture_cli_help.py` |
| `pipeline/` | 批量转录与术语修订管线 | `pipeline/batch_transcribe.py` |
| `tools/` | 辅助工具（下载 / 抓取 / URL 解析 / 格式转换） | `tools/get_user_url.py` |
| `launch/` | 一键启动 Web UI（跨平台） | `launch/one_click_up.py` |
| `gates/` | 提交前门禁 | `gates/pre_push_gate.py` |

顶层两个日常入口：

| 脚本 | 用途 |
|---|---|
| `run_tests.py` | pytest 转发垫片，参数与 CI unit job 一致；额外参数原样透传 |
| `demo_v3.py` | v3 功能的离线演示（不需要浏览器） |

---

## bench/ — 基准

| 脚本 | 用途 |
|---|---|
| `bench/benchmark_transcribers.py` | 引擎基准（schema `mediascribe-benchmark/v1`）。⚠️ 其真引擎路径长期是 `NotImplementedError`，真实引擎实测已迁到 `bench/asr/bench_asr.py` |
| `bench/asr/bench_asr.py` | L3 推理引擎基准（faster-whisper / openai-whisper）。**硬件自适应 + 资源护栏 + 进度提示 + 断点续跑** |
| `bench/asr/bench_whispercpp.py` | whisper.cpp（C3）基准，与上者同语料同 CER 口径 |
| `bench/asr/zh_normalize.py` | 中文繁简归一化（Windows `LCMapStringW`，零依赖）。**不归一化会把字形差异误判成识别错误**（实测 3.21% ↔ 33.61%） |
| `bench/asr/summarize_asr.py` | 汇总 ASR 基准结果（严格区分成功轮 / 未完成轮，避免失败轮的 RTF 污染统计） |
| `bench/asr/recompute_cer.py` | 按当前口径重算已落盘轮次的 CER（**不重跑推理**） |
| `bench/corpus/gen_corpus_real.py` | **真实语音**基准语料生成器（Windows SAPI 离线合成，16 kHz mono）。固定 `anoisesrc` 种子 ⇒ 两次生成 SHA-256 一致；配套 `corpus/sapi_tts.ps1` |
| `bench/corpus/gen_corpus.py` | 正弦波占位语料生成器（仅用于流水线自检，非真实语音） |
| `bench/frontend/scaffold_candidates.py` | 生成 L1 五个前端候选工程（vanilla/react/vue/svelte/solid），统一 DOM 结构与埋点协议 |
| `bench/frontend/bench_frontend.py` | L1 前端基准：构建时长 / 产物体积 / 依赖规模 / 冷启动 / 渲染 / 筛选 / 滚动帧率 |
| `bench/shell/bench_shell.py` | L2 工作台壳基准（B1 FastAPI+浏览器 / B2 预构建静态站点 / B3 Tauri / B5 WASM） |

### 基准产出位置

| 目录 | 内容 | 入库 |
|---|---|---|
| `output/bench/corpus/` | 语料音频 + reference JSON + manifest | ❌ gitignored |
| `output/bench/asr/rounds/` | 每轮一个 JSON（峰值显存/内存/温度/RTF/错误率） | ❌ gitignored |
| `output/bench/frontend/` | 候选工程 + 构建产物 + 测试数据 | ❌ gitignored |
| `output/bench/shell/` | 工作台壳层的应用工程与产物 | ❌ gitignored |
| `scripts/bench/*/results.json` | **汇总结果**（入库，可复盘） | ✅ |

---

## e2e/ — 真实网络验证与快照

| 脚本 | 用途 |
|---|---|
| `e2e/capture_cli_help.py` | 抓取 CLI help 快照到 `docs/cli-screenshots/`（输出目录不入库，CLI 面变化后本地核对用） |
| `e2e/run_real_e2e.py` | 真实 URL 的端到端验证（真实网络 + 真实 ASR） |
| `e2e/run_wechat_mp_e2e.py` | 微信公众号文章端到端验证，用 `e2e/wechat_mp_urls.txt` 作 fixture |
| `e2e/capture_e2e_recording.py` | 录制 E2E 运行过程（终端日志 + 产物归档） |

---

## pipeline/ — 批量转录与术语修订

| 脚本 | 用途 |
|---|---|
| `pipeline/batch_transcribe.py` | 多 P 课程批量转录。`--bv <BV号> --total <集数>`，large-v3 + CUDA + 时间戳 + `--audio-only`；启动前做分 P 防呆断言（数量/唯一性/序号对应），断点续跑（断点 `output/.batch_state_<BV>.json`） |
| `pipeline/fix_xutao_terms.py` | ASR 术语批量替换（修订管线第①步）。规则表分批次记录，`TERMS_STATE` 环境变量切换目标课程断点文件；幂等可重跑 |
| `pipeline/seed_learned_terms.py` | 把人工核定的修正表灌入 learn 术语库（`learned_terms.json`），此后转录时自动注入 initial_prompt + 事后校正，从源头减少术语错字 |
| `pipeline/process_douyin.py` | 单条抖音链接的最小处理示例（脚本顶部硬编码了待处理链接，改用即改） |

---

## tools/ — 辅助工具

| 脚本 | 用途 |
|---|---|
| `tools/get_user_url.py` | 从分享文案里提取规范化的作者主页 / 视频 URL |
| `tools/douyin_user_scraper.py` | 抓取作者主页的视频列表 |
| `tools/douyin_auto_download.py` | 按列表批量下载媒体文件 |
| `tools/convert_to_md.py` | 把已有的 `.txt` 转录稿转换成 `.md`（读 `output/transcripts/`） |

---

## launch/ — 一键启动

| 脚本 | 用途 |
|---|---|
| `launch/one_click_up.py` | 跨平台启动器（Python 实现，**唯一事实源**）：`up` / `up --daemon` / `stop` / `status` / `--mode docker` |
| `launch/one_click_up.sh` | POSIX shell 薄包装（macOS / Linux / Git Bash） |
| `launch/one_click_up.ps1` | PowerShell 薄包装（Windows） |

包装脚本与 Python 实现**同目录**，靠 `SCRIPT_DIR` 相对定位，不要拆开。

---

## gates/ — 提交前门禁

| 脚本 | 用途 |
|---|---|
| `gates/pre_push_gate.py` | pre-push 门禁（2026-10-03）：ruff + CI 同参快速测试，任何失败非零退出。`python scripts/gates/pre_push_gate.py`；亦可用 `just push` / `make push` 一键过门禁后推送 |

---

## 标准流程（新课程 / 新增分 P）

1. `python scripts/pipeline/batch_transcribe.py --bv <BV> --total <N>`
2. 术语扫描与替换：`TERMS_STATE=.batch_state_<BV>.json python scripts/pipeline/fix_xutao_terms.py`（新误识词追加进规则表）
3. 幻觉重复块核对（管线内已自动收敛 ≥3 连，残余跨段重叠手工处理）
4. 尾部核对：逐集比对其末段时间戳与音频时长，缺口 >40s 时抽尾部重转写对比
5. 审校稿按 `output/wiki/` 统一格式整理，归档到课程同名子文件夹
