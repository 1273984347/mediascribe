"""双扫描器黄金快照 (搁置清单 #1 前置): 现行实现的输出逐字节钉死。

2026-11-03 生成自重构前的 detect_repeated_pairs / detect_repeated_pairs_detailed
(电池 = 既有测试定标样本 + 边界输入, 60 条)。合并为参数化引擎后本文件
必须原样通过 — 任何输出差异都是静默漂移。
"""

from __future__ import annotations

import pytest

from mediascribe.post_process import detect_repeated_pairs, detect_repeated_pairs_detailed

GOLDEN = {
 "": {
  "simple": [],
  "detailed": []
 },
 "   \n  \n": {
  "simple": [],
  "detailed": []
 },
 "这是一个完全没有重复的普通句子。第二句话讲别的事情。第三句继续。": {
  "simple": [],
  "detailed": []
 },
 "考北大,考北大。": {
  "simple": [],
  "detailed": []
 },
 "你要写在第一个分论点,你就偏题了。写在第一个分论点。你要写在第一个分论点,你就偏题了。": {
  "simple": [
   "你要写在第一个分论点,"
  ],
  "detailed": [
   {
    "text": "你要写在第一个分论点,",
    "kind": "echo"
   }
  ]
 },
 "所以,在最后的升华点的时候,我们一定能够找到它。所以,在最后的升华点的时候,我们一定能够找到它。后面还有新内容。": {
  "simple": [
   "所以,在最后的升华点的时候,我们一定能够找到它。"
  ],
  "detailed": [
   {
    "text": "所以,在最后的升华点的时候,我们一定能够找到它。",
    "kind": "artifact"
   }
  ]
 },
 "在终点的地方被定义的, 而是在终点的地方被定义的": {
  "simple": [],
  "detailed": []
 },
 "同一句重复三遍。同一句重复三遍。同一句重复三遍。后续内容。": {
  "simple": [],
  "detailed": []
 },
 "短句,重复。短句,重复。": {
  "simple": [],
  "detailed": []
 },
 "前半段完全一样的内容然后结尾不同啊。前半段完全一样的内容然后结尾不同吗？": {
  "simple": [],
  "detailed": []
 },
 "第一行结尾的句子\n第一行结尾的句子": {
  "simple": [
   "第一行结尾的句子"
  ],
  "detailed": [
   {
    "text": "第一行结尾的句子",
    "kind": "artifact"
   }
  ]
 },
 "上段的最后一句。\n\n上段的最后一句。": {
  "simple": [
   "上段的最后一句。"
  ],
  "detailed": [
   {
    "text": "上段的最后一句。",
    "kind": "artifact"
   }
  ]
 },
 "A, A。B正常继续。": {
  "simple": [],
  "detailed": []
 },
 "English mixed with 中文, English mixed with 中文!": {
  "simple": [],
  "detailed": []
 },
 "过度坦诚一集真应答:你说得对。你说得对,但是下一句是转折。新话题开始。": {
  "simple": [],
  "detailed": []
 },
 "标点…省略号重复。标点…省略号重复。": {
  "simple": [
   "标点…省略号重复。"
  ],
  "detailed": [
   {
    "text": "标点…省略号重复。",
    "kind": "artifact"
   }
  ]
 },
 "一句没有任何标点符号的很长短语循环出现测试 一句没有任何标点符号的很长短语循环出现测试": {
  "simple": [
   "一句没有任何标点符号的很长短语循环出现测试"
  ],
  "detailed": [
   {
    "text": "一句没有任何标点符号的很长短语循环出现测试",
    "kind": "artifact"
   }
  ]
 },
 "行1的独立单元,行1的独立单元。\n行2的独立单元,行2的独立单元。": {
  "simple": [
   "行1的独立单元,",
   "行2的独立单元,"
  ],
  "detailed": [
   {
    "text": "行1的独立单元,",
    "kind": "artifact"
   },
   {
    "text": "行2的独立单元,",
    "kind": "artifact"
   }
  ]
 },
 "尾缀重叠八字以上的第一遍说到这里为止了。尾缀重叠八字以上的第一遍说到这里为止了才怪。": {
  "simple": [],
  "detailed": []
 },
 "只有一句话。": {
  "simple": [],
  "detailed": []
 },
 "重复。重复。重复。重复。": {
  "simple": [],
  "detailed": []
 },
 "这个单元很短。这个单元很短,但后半句不同所以更长一些。": {
  "simple": [
   "这个单元很短。"
  ],
  "detailed": [
   {
    "text": "这个单元很短。",
    "kind": "echo"
   }
  ]
 },
 "v3.4.3 2 连重复句检测(detect_repeated_pairs)与 seed 术语库数据卫生测试": {
  "simple": [],
  "detailed": []
 },
 "我今天很难过,我今天很难过,这没有任何办法。": {
  "simple": [
   "我今天很难过,"
  ],
  "detailed": [
   {
    "text": "我今天很难过,",
    "kind": "artifact"
   }
  ]
 },
 "我们要活在当下,活在当下,这才是最重要的。": {
  "simple": [],
  "detailed": []
 },
 "他说的就是这句话,他说的就是这句话,他说的就是这句话,结束了。": {
  "simple": [],
  "detailed": []
 },
 "真正的话说一遍就够了。真正的话说一遍就够了。真正的话说一遍就够了。": {
  "simple": [],
  "detailed": []
 },
 "但是我还要说,但是我还要说,完了。": {
  "simple": [
   "但是我还要说,"
  ],
  "detailed": [
   {
    "text": "但是我还要说,",
    "kind": "artifact"
   }
  ]
 },
 "是因为我自己没有办法去考北大": {
  "simple": [],
  "detailed": []
 },
 "我不会产出这个期待,是因为我自己没有办法去考北大,": {
  "simple": [],
  "detailed": []
 },
 "是因为我自己没有办法去考北大,所以说我希望你去考北大。": {
  "simple": [],
  "detailed": []
 },
 "所以,在最后的升华点的时候,我们一定能够找到它。": {
  "simple": [],
  "detailed": []
 },
 "所以,在最后的升华点的时候,我们一定要强调。": {
  "simple": [],
  "detailed": []
 },
 "所以,在最后的升华点的时候,我们一定要强调。\\n": {
  "simple": [],
  "detailed": []
 },
 "正因为如此,我们才能够更加诚实地去分辨,这些改变究竟让我们更有担当,": {
  "simple": [],
  "detailed": []
 },
 "还是让我们越来越善于为冷漠寻找理由。\\n": {
  "simple": [],
  "detailed": []
 },
 "还是让我们越来越善于为冷漠寻找理由。": {
  "simple": [],
  "detailed": []
 },
 "echo/artifact 启发式标注 — 用四个真实校准样本定标。": {
  "simple": [],
  "detailed": []
 },
 "有很多同学就很积极说,老师,我要把那个对自己坦诚写在第一个分论点。": {
  "simple": [],
  "detailed": []
 },
 "你要写在第一个分论点,你就偏题。": {
  "simple": [],
  "detailed": []
 },
 "我之前跟你说过这件事。但是这件事你从来没听进去,这件事你从来没听进去,真可惜。": {
  "simple": [
   "这件事你从来没听进去,"
  ],
  "detailed": [
   {
    "text": "这件事你从来没听进去,",
    "kind": "echo"
   }
  ]
 },
 "第一行重复内容,第一行重复内容。\\n第二行:第一行重复内容,第一行重复内容。": {
  "simple": [
   "第一行重复内容,"
  ],
  "detailed": [
   {
    "text": "第一行重复内容,",
    "kind": "artifact"
   }
  ]
 },
 "生命从来都不是在终点的地方被定义的,而是在终点的地方被定义的,": {
  "simple": [
   "而是在终点的地方被定义的,"
  ],
  "detailed": [
   {
    "text": "而是在终点的地方被定义的,",
    "kind": "artifact"
   }
  ]
 },
 "而是在每一个今天的日升月落之中被雕刻的。": {
  "simple": [],
  "detailed": []
 },
 "他昨天去了北京,我今天也去了北京。": {
  "simple": [],
  "detailed": []
 },
 "我知道什么是对的,什么是对的。": {
  "simple": [],
  "detailed": []
 },
 "seed 脚本数据卫生: wrong/right 非空、不同、无重复对。": {
  "simple": [],
  "detailed": []
 },
 "空项: {(wrong, right)}": {
  "simple": [],
  "detailed": []
 },
 "重复对: {key}": {
  "simple": [],
  "detailed": []
 },
 "幻觉重复收敛(collapse_hallucination_repeats)与 banner 附注测试": {
  "simple": [],
  "detailed": []
 },
 "第一句,这个世界是怎样存在的,这个世界是怎样存在的,这个世界是怎样存在的,": {
  "simple": [],
  "detailed": []
 },
 "这个世界是怎样存在的,这个世界是怎样存在的呢?结尾语。": {
  "simple": [],
  "detailed": []
 },
 "老师强调,对的对的,这是口语强调,没问题。": {
  "simple": [],
  "detailed": []
 },
 "我们眼前有一朵花,我们眼前有一": {
  "simple": [],
  "detailed": []
 },
 "第一段内容。\\n第二段,我们坚持下去了,我们坚持下去了,我们坚持下去了,我们坚持下去了,继续。": {
  "simple": [],
  "detailed": []
 },
 "第一段内容。\\n第二段,我们坚持下去了,继续。": {
  "simple": [],
  "detailed": []
 },
 "啊,啊,啊,啊,啊,好的,好的,好的,好的,这不能算幻觉。": {
  "simple": [],
  "detailed": []
 },
 "他好像说的就是,面对巨大的幸福就需要很大很大的内力。": {
  "simple": [],
  "detailed": []
 },
 "因为你会发现,他说人在面对巨大的幸福的时候会胆怯,因为我们没有勇气。": {
  "simple": [],
  "detailed": []
 },
 "我们总以为抓住巨大的幸福需要的是那一刻的勇气。": {
  "simple": [],
  "detailed": []
 }
}


@pytest.mark.parametrize("text", list(GOLDEN))
def test_simple_matches_golden(text):
    assert detect_repeated_pairs(text) == GOLDEN[text]["simple"]


@pytest.mark.parametrize("text", list(GOLDEN))
def test_detailed_matches_golden(text):
    assert detect_repeated_pairs_detailed(text) == GOLDEN[text]["detailed"]
