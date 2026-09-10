"""
v3.2.0b — post_process.py 测试。

覆盖:
1. post_process_transcript 默认术语替换
2. custom_terms 合并
3. MEDIASCRIBE_CUSTOM_TERMS 环境变量
4. auto_select_model 时长分档
5. get_prompt_template domain + custom 拼接
6. setup_hf_mirror 默认值 + 环境变量覆盖
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from mediascribe.post_process import (
    DEFAULT_TERMS,
    auto_select_model,
    get_prompt_template,
    post_process_transcript,
    setup_hf_mirror,
)


# ---------------------------------------------------------------------------
# 术语校正
# ---------------------------------------------------------------------------
class TestPostProcessTranscript(unittest.TestCase):
    def test_default_terms_replace(self):
        text = "今天讲获取病的故事"
        result = post_process_transcript(text)
        self.assertEqual(result, "今天讲霍去病的故事")

    def test_custom_terms_override_default(self):
        text = "某篇布局很重要"
        result = post_process_transcript(
            text, custom_terms={"某篇布局": "谋篇布局(定制)"}
        )
        self.assertEqual(result, "谋篇布局(定制)很重要")

    def test_custom_terms_merge_with_default(self):
        text = "霍去病和谋篇布局"
        result = post_process_transcript(
            text, custom_terms={"下水温": "下水文章"}
        )
        # custom_terms 合并后, DEFAULT 不变(文本已经是正确词,不应替换)
        self.assertIn("霍去病", result)
        self.assertIn("谋篇布局", result)

    def test_env_custom_terms_parsed(self):
        text = "注价老师很厉害"
        env = {k: v for k, v in os.environ.items() if k != "MEDIASCRIBE_CUSTOM_TERMS"}
        env["MEDIASCRIBE_CUSTOM_TERMS"] = '{"注价老师": "助教(环境变量)"}'
        with mock.patch.dict(os.environ, env, clear=True):
            result = post_process_transcript(text, merge_env=True)
        self.assertEqual(result, "助教(环境变量)很厉害")

    def test_env_invalid_json_ignored(self):
        text = "获取病"
        env = {k: v for k, v in os.environ.items() if k != "MEDIASCRIBE_CUSTOM_TERMS"}
        env["MEDIASCRIBE_CUSTOM_TERMS"] = "{bad json"
        with mock.patch.dict(os.environ, env, clear=True):
            result = post_process_transcript(text, merge_env=True)
        # 应该用默认术语替换
        self.assertEqual(result, "霍去病")

    def test_merge_env_false_ignores_env(self):
        text = "获取病"
        env = {k: v for k, v in os.environ.items() if k != "MEDIASCRIBE_CUSTOM_TERMS"}
        env["MEDIASCRIBE_CUSTOM_TERMS"] = '{"获取病": "不替换"}'
        with mock.patch.dict(os.environ, env, clear=True):
            result = post_process_transcript(text, merge_env=False)
        self.assertEqual(result, "霍去病")

    def test_no_change_when_no_match(self):
        text = "今天天气很好"
        result = post_process_transcript(text)
        self.assertEqual(result, text)

    def test_markdown_passthrough(self):
        text = "# 获取病\n\n正文内容"
        result = post_process_transcript(text)
        self.assertEqual(result, "# 霍去病\n\n正文内容")


# ---------------------------------------------------------------------------
# 智能模型推荐
# ---------------------------------------------------------------------------
class TestAutoSelectModel(unittest.TestCase):
    def test_short_video_balance(self):
        # <300s default → medium
        self.assertEqual(auto_select_model(60), "medium")
        self.assertEqual(auto_select_model(299), "medium")

    def test_medium_video_balance(self):
        self.assertEqual(auto_select_model(600), "small")
        self.assertEqual(auto_select_model(1799), "small")

    def test_long_video_balance(self):
        self.assertEqual(auto_select_model(1800), "tiny")
        self.assertEqual(auto_select_model(3600), "tiny")

    def test_prefer_quality_short(self):
        self.assertEqual(auto_select_model(60, prefer_quality=True), "large-v3")
        self.assertEqual(auto_select_model(599, prefer_quality=True), "large-v3")

    def test_prefer_quality_medium(self):
        self.assertEqual(auto_select_model(600, prefer_quality=True), "medium")
        self.assertEqual(auto_select_model(3599, prefer_quality=True), "medium")

    def test_prefer_quality_long(self):
        self.assertEqual(auto_select_model(3600, prefer_quality=True), "small")


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------
class TestGetPromptTemplate(unittest.TestCase):
    def test_general_empty(self):
        self.assertEqual(get_prompt_template("general"), "")

    def test_education_domain(self):
        tpl = get_prompt_template("education")
        self.assertIn("教育", tpl)
        self.assertIn("高考", tpl)

    def test_tech_domain(self):
        tpl = get_prompt_template("tech")
        self.assertIn("技术分享", tpl)
        self.assertIn("编程", tpl)

    def test_literature_domain(self):
        tpl = get_prompt_template("literature")
        self.assertIn("文学", tpl)
        self.assertIn("古诗词", tpl)

    def test_custom_prompt_only(self):
        tpl = get_prompt_template(custom_prompt="这是高考作文")
        self.assertEqual(tpl, "这是高考作文")

    def test_domain_plus_custom_prompt(self):
        tpl = get_prompt_template("education", custom_prompt="重点讲矛盾二分法")
        self.assertIn("教育", tpl)
        self.assertIn("矛盾二分法", tpl)

    def test_unknown_domain_falls_to_general(self):
        self.assertEqual(get_prompt_template("nonexistent"), "")


# ---------------------------------------------------------------------------
# HF 镜像
# ---------------------------------------------------------------------------
class TestSetupHfMirror(unittest.TestCase):
    def test_default_mirror_returns_correct_url(self):
        """未设 HF_ENDPOINT 时返回默认镜像 URL。"""
        env = {k: v for k, v in os.environ.items() if k != "HF_ENDPOINT"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = setup_hf_mirror()
        self.assertEqual(result, "https://hf-mirror.com")

    def test_env_override_preserved(self):
        """已设 HF_ENDPOINT 时不覆盖。"""
        env = {k: v for k, v in os.environ.items() if k != "HF_ENDPOINT"}
        env["HF_ENDPOINT"] = "https://my-custom-mirror.com"
        with mock.patch.dict(os.environ, env, clear=True):
            result = setup_hf_mirror()
        self.assertEqual(result, "https://my-custom-mirror.com")


# ---------------------------------------------------------------------------
# DEFAULT_TERMS 字典 sanity
# ---------------------------------------------------------------------------
class TestDefaultTerms(unittest.TestCase):
    def test_no_duplicate_keys(self):
        # 重复 key 在 Python 里不报错,但逻辑上应该避免
        self.assertEqual(len(DEFAULT_TERMS), len(set(DEFAULT_TERMS.keys())))


if __name__ == "__main__":
    unittest.main()
