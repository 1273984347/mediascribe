# scripts/ — 转录流水线工具

MediScribe 主流程之外的批量任务与修订工具(在 45 集徐涛课程两门课的实战中沉淀)。

| 脚本 | 用途 |
|---|---|
| `batch_transcribe.py` | 多 P 课程批量转录。`--bv <BV号> --total <集数>`,large-v3 + CUDA + 时间戳 + `--audio-only`;启动前做分 P 防呆断言(数量/唯一性/序号对应),断点续跑(断点 `output/.batch_state_<BV>.json`) |
| `fix_xutao_terms.py` | ASR 术语批量替换(修订管线第①步)。规则表分批次记录,`TERMS_STATE` 环境变量切换目标课程断点文件;幂等可重跑 |
| `seed_learned_terms.py` | 把人工核定的修正表灌入 learn 术语库(`learned_terms.json`),此后转录时自动注入 initial_prompt + 事后校正,从源头减少术语错字 |

## 标准流程(新课程/新增分 P)

1. `python scripts/batch_transcribe.py --bv <BV> --total <N>`
2. 术语扫描与替换:`TERMS_STATE=.batch_state_<BV>.json python scripts/fix_xutao_terms.py`(新误识词追加进规则表)
3. 幻觉重复块核对(管线内已自动收敛 ≥3 连,残余跨段重叠手工处理)
4. 尾部核对:逐集比对其末段时间戳与音频时长,缺口 >40s 时抽尾部重转写对比
5. 审校稿按 `output/wiki/` 统一格式整理,归档到课程同名子文件夹
