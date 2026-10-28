"""模型名单的**跨入口一致性**测试（引擎版的姊妹篇，见 test_engine_consistency.py）。

背景
----
引擎名单的四处漂移曾造成真实事故（Web 层 422 拒绝 whisper-cpp，457065f 修复
后加了 test_engine_consistency.py）。模型名单是**同型事故的下一次发生地**：
``config._VALID_MODELS`` / CLI ``--model`` choices / ``web`` 层 pydantic 正则 /
``index.html`` 下拉 / ``model_weights`` 预置清单，五处独立维护、此前零测试。
2026-10-29 对抗性审查 P2 将其钉死。

口径约定（故意的，不是漂移）：
- ``config._VALID_MODELS`` = 具体档位（10 个，无 ``auto``）
- CLI choices / index.html 首选项 = ``auto`` + 具体档位（入口层支持自动推荐）
- web pydantic pattern = 仅具体档位（提交时已由后端解析 auto）
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mediascribe.config import _VALID_MODELS  # noqa: E402


def _cli_model_choices() -> set:
    """从 ``mediascribe/__main__.py`` 解析 ``--model`` 的 choices。"""
    source = (ROOT / "mediascribe" / "__main__.py").read_text(encoding="utf-8")
    match = re.search(r'"--model".*?choices=\[(.*?)\]', source, re.S)
    assert match, "未能在 __main__.py 里定位 --model 的 choices"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _web_model_pattern() -> set:
    """从 ``mediascribe/web/models.py`` 解析 model 的 pydantic 正则。"""
    source = (ROOT / "mediascribe" / "web" / "models.py").read_text(encoding="utf-8")
    match = re.search(r'model: str = Field\(\s*"small",\s*pattern="\^?(.*?)\$"', source, re.S)
    assert match, "未能在 web/models.py 里定位 model 的 pattern"
    raw = match.group(1).replace("(", "").replace(")", "")
    return {part for part in raw.split("|") if part}


def _index_html_model_options() -> set:
    """解析 index.html 的 #model 下拉 value 集合。"""
    html = (ROOT / "mediascribe" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    select = re.search(r'<select id="model">.*?</select>', html, re.S)
    assert select, "index.html 缺少 #model 下拉"
    return set(re.findall(r'<option value="([^"]+)"', select.group(0)))


def _mcp_ocr_enum() -> set:
    """从 mcp_server.py 解析 ocr_engine 的 JSON schema enum。"""
    source = (ROOT / "mediascribe" / "mcp_server.py").read_text(encoding="utf-8")
    match = re.search(r'"ocr_engine":\s*\{\s*"type": "string",\s*"enum": \[(.*?)\]', source, re.S)
    assert match, "未能在 mcp_server.py 里定位 ocr_engine enum"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _web_ocr_pattern() -> set:
    """从 web/models.py 解析 ocr_engine 的 pydantic 正则。"""
    source = (ROOT / "mediascribe" / "web" / "models.py").read_text(encoding="utf-8")
    match = re.search(r'ocr_engine: str = Field\("auto", pattern="\^?(.*?)\$"\)', source)
    assert match, "未能在 web/models.py 里定位 ocr_engine 的 pattern"
    raw = match.group(1).replace("(", "").replace(")", "")
    return {part for part in raw.split("|") if part}


class TestModelNameConsistency(unittest.TestCase):
    """五处模型名单必须一致（口径差异见模块 docstring）。"""

    def test_cli_choices_are_auto_plus_valid(self):
        self.assertEqual(_cli_model_choices(), {"auto"} | set(_VALID_MODELS))

    def test_web_pattern_matches_valid_models(self):
        self.assertEqual(_web_model_pattern(), set(_VALID_MODELS))

    def test_index_html_dropdown_covers_valid_models(self):
        options = _index_html_model_options()
        for model in _VALID_MODELS:
            self.assertIn(model, options, msg=f"#model 下拉缺少 {model}")

    def test_known_weight_pairs_cover_all_valid_models(self):
        from mediascribe.model_weights import known_weight_pairs

        paired = {model for _, model in known_weight_pairs()}
        self.assertEqual(paired, set(_VALID_MODELS))


class TestOcrEngineConsistency(unittest.TestCase):
    """MCP 的 ocr_engine enum 与 Web 层 pattern 必须一致（MCP 曾缺 ``none``）。"""

    def test_mcp_enum_matches_web_pattern(self):
        self.assertEqual(_mcp_ocr_enum(), _web_ocr_pattern())

    def test_none_is_an_allowed_value(self):
        self.assertIn("none", _mcp_ocr_enum())


if __name__ == "__main__":
    unittest.main()
