# scripts/ — 转录流水线工具

MediScribe 主流程之外的批量任务与修订工具(在 45 集徐涛课程两门课的实战中沉淀)。

| 脚本 | 用途 |
|---|---|
| `batch_transcribe.py` | 多 P 课程批量转录。`--bv <BV号> --total <集数>`,large-v3 + CUDA + 时间戳 + `--audio-only`;启动前做分 P 防呆断言(数量/唯一性/序号对应),断点续跑(断点 `output/.batch_state_<BV>.json`) |
| `fix_xutao_terms.py` | ASR 术语批量替换(修订管线第①步)。规则表分批次记录,`TERMS_STATE` 环境变量切换目标课程断点文件;幂等可重跑 |
| `seed_learned_terms.py` | 把人工核定的修正表灌入 learn 术语库(`learned_terms.json`),此后转录时自动注入 initial_prompt + 事后校正,从源头减少术语错字 |
| `pre_push_gate.py` | pre-push 门禁(2026-10-03):ruff + CI 同参快速测试,任何失败非零退出。`python scripts/pre_push_gate.py`;亦可用 `just push` / `make push` 一键过门禁后推送 |
| `capture_cli_help.py` | 重建 `docs/cli-screenshots/` 的 CLI help 快照(CLI 面变化后跑一次) |
| `benchmark_transcribers.py` | 引擎基准(schema `mediascribe-benchmark/v1`)。⚠️ 其真引擎路径长期是 `NotImplementedError`,真实引擎实测已迁到 `bench/asr/bench_asr.py` |
| `benchmark_corpus/gen_corpus_real.py` | **真实语音**基准语料生成器(Windows SAPI 离线合成,16 kHz mono)。固定 `anoisesrc` 种子 ⇒ 两次生成 SHA-256 一致;配套 `sapi_tts.ps1` |
| `bench/asr/bench_asr.py` | L3 推理引擎基准(faster-whisper / openai-whisper)。**硬件自适应 + 资源护栏 + 进度提示 + 断点续跑** |
| `bench/asr/bench_whispercpp.py` | whisper.cpp(C3)基准,与上者同语料同 CER 口径 |
| `bench/asr/zh_normalize.py` | 中文繁简归一化(Windows `LCMapStringW`,零依赖)。**不归一化会把字形差异误判成识别错误**(实测 3.21% ↔ 33.61%) |
| `bench/asr/summarize_asr.py` | 汇总 ASR 基准结果(严格区分成功轮 / 未完成轮,避免失败轮的 RTF 污染统计) |
| `bench/asr/recompute_cer.py` | 按当前口径重算已落盘轮次的 CER(**不重跑推理**) |
| `bench/frontend/scaffold_candidates.py` | 生成 L1 五个前端候选工程(vanilla/react/vue/svelte/solid),统一 DOM 结构与埋点协议 |
| `bench/frontend/bench_frontend.py` | L1 前端基准:构建时长 / 产物体积 / 依赖规模 / 冷启动 / 渲染 / 筛选 / 滚动帧率 |

## 基准产出位置

| 目录 | 内容 | 入库 |
|---|---|---|
| `output/bench/corpus/` | 语料音频 + reference JSON + manifest | ❌ gitignored |
| `output/bench/asr/rounds/` | 每轮一个 JSON(峰值显存/内存/温度/RTF/错误率) | ❌ gitignored |
| `output/bench/frontend/` | 候选工程 + 构建产物 + 测试数据 | ❌ gitignored |
| `output/bench/shell/` | 工作台壳层的应用工程与产物 | ❌ gitignored |
| `scripts/bench/*/results.json` | **汇总结果**(入库,可复盘) | ✅ |
| `scripts/bench/frontend/results.json` | L1 汇总结果(入库) | ✅ |

## 标准流程(新课程/新增分 P)

1. `python scripts/batch_transcribe.py --bv <BV> --total <N>`
2. 术语扫描与替换:`TERMS_STATE=.batch_state_<BV>.json python scripts/fix_xutao_terms.py`(新误识词追加进规则表)
3. 幻觉重复块核对(管线内已自动收敛 ≥3 连,残余跨段重叠手工处理)
4. 尾部核对:逐集比对其末段时间戳与音频时长,缺口 >40s 时抽尾部重转写对比
5. 审校稿按 `output/wiki/` 统一格式整理,归档到课程同名子文件夹
