"""把人工核定的 ASR 修正表灌入 learn 术语库 (learned_terms.json)

用法:
    python scripts/seed_learned_terms.py            # 幂等, 已存在则跳过
    python scripts/seed_learned_terms.py --force    # 追加/确认(不删除已有)

效果:
- get_prompt_terms() 把正确词注入 Whisper initial_prompt (转录时防错)
- post_process_transcript(merge_learned=True) 事后自动替换 (兜底)

只收录"错误写法在汉语中不存在或在本领域必然错误"的高置信对;
像 分支/正经/本源/食物 这类真实词的语境级修正不放进来(有误伤风险)。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mediascribe.learn import LearnedTermsDB, learned_terms_path  # noqa: E402

# 人工核定 (2026-09-15, 徐涛考研政治 45 集两门课程实测)
# (wrong, right)
SAFE_TERMS = [
    # 学科简称
    ("马元", "马原"),
    ("马源", "马原"),
    ("马园", "马原"),
    ("马圆", "马原"),
    ("毛宗特", "毛中特"),
    ("宗特", "中特"),
    ("史刚", "史纲"),
    ("科设", "科社"),
    ("马则", "马哲"),
    # 哲学概念
    ("维吾论", "唯物论"),
    ("行而上学", "形而上学"),
    ("行而上", "形而上"),
    ("维新史观", "唯心史观"),
    ("维吾史观", "唯物史观"),
    ("伪务史观", "唯物史观"),
    ("能动反应", "能动反映"),
    ("质量互辨定律", "质量互变规律"),
    ("废畜百家", "罢黜百家"),
    ("零零总总", "林林总总"),
    ("向由心生", "相由心生"),
    # 高频词/专名
    ("研究生录学考试", "研究生入学考试"),
    ("换颜值", "换言之"),
    ("盐友", "研友"),
    ("主卫兵", "主谓宾"),
    ("事如破竹", "势如破竹"),
    ("沉门失火", "城门失火"),
    ("利益器", "利器"),
    ("自持其利", "自食其力"),
    ("反地反封建", "反帝反封建"),
    ("身搬硬套", "生搬硬套"),
    ("三大根据", "三大改造"),
]

# 思修法基的同音变体(程序展开)
for _v in ("私修法级", "私修法系", "思修法级", "思绪法机", "司休法机", "思绪法基"):
    SAFE_TERMS.append((_v, "思修法基"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="已存在时仍追加/确认")
    args = parser.parse_args()

    store = learned_terms_path()
    if store.exists() and not args.force:
        print(f"术语库已存在: {store}\n如需追加/确认请加 --force")
        return 0

    db = LearnedTermsDB()
    if store.exists():
        import json

        db = LearnedTermsDB(**json.loads(store.read_text(encoding="utf-8")))

    added = 0
    for wrong, right in SAFE_TERMS:
        if not wrong or not right:
            continue
        db.add(wrong, right)
        db.confirm(wrong, right)  # 人工核定, 直接确认
        added += 1

    from mediascribe.learn import _save_db

    _save_db(db)
    print(f"已灌入 {added} 条确认术语 -> {store}")

    from mediascribe.learn import get_prompt_terms

    preview = get_prompt_terms()
    print(f"\nprompt 注入预览:\n  {preview[:180]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
