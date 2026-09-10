"""v3.2.0d — LLM 后处理模块测试。

5 类场景：
1. ``llm-disabled`` — 未设 api_key 或 enabled=False
2. ``llm-skipped`` — 文本过短 (< 50 chars)
3. ``llm-reviewed`` — API 成功，返回修正文本
4. ``llm-failed`` — API 抛错，回退原文
5. ``from_env`` / ``from_settings`` — 配置构造正确

Mock 策略：用 ``unittest.mock.patch`` 替换 ``openai.OpenAI`` 客户端的
``chat.completions.create`` 方法，避免真实 API 调用。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _make_response(content: str) -> SimpleNamespace:
    """构造 OpenAI ChatCompletion 响应 mock。"""
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


class TestLLMPostProcessorDisabled(unittest.TestCase):
    """未启用 / 无 api_key → 返回原文 + llm-disabled。"""

    def test_no_api_key_returns_disabled(self):
        from mediascribe.llm_post_process import STATUS_DISABLED, LLMPostProcessor
        proc = LLMPostProcessor(api_key="", enabled=True)
        text, status = proc.post_process("这是一段足够长的中文文本用于测试。" * 5)
        self.assertEqual(status, STATUS_DISABLED)
        self.assertIn("这是一段", text)

    def test_enabled_false_returns_disabled(self):
        from mediascribe.llm_post_process import STATUS_DISABLED, LLMPostProcessor
        proc = LLMPostProcessor(api_key="fake-key", enabled=False)
        text, status = proc.post_process("这是一段足够长的中文文本用于测试。" * 5)
        self.assertEqual(status, STATUS_DISABLED)


class TestLLMPostProcessorSkipped(unittest.TestCase):
    """文本过短 → 返回原文 + llm-skipped。"""

    def test_short_text_returns_skipped(self):
        from mediascribe.llm_post_process import STATUS_SKIPPED, LLMPostProcessor
        proc = LLMPostProcessor(api_key="fake-key", enabled=True)
        text, status = proc.post_process("短文本")
        self.assertEqual(status, STATUS_SKIPPED)
        self.assertEqual(text, "短文本")

    def test_empty_text_returns_skipped(self):
        from mediascribe.llm_post_process import STATUS_SKIPPED, LLMPostProcessor
        proc = LLMPostProcessor(api_key="fake-key", enabled=True)
        text, status = proc.post_process("")
        self.assertEqual(status, STATUS_SKIPPED)


class TestLLMPostProcessorReviewed(unittest.TestCase):
    """API 成功 → 返回修正文本 + llm-reviewed。"""

    def test_successful_call_returns_reviewed(self):
        from mediascribe.llm_post_process import STATUS_REVIEWED, LLMPostProcessor
        proc = LLMPostProcessor(api_key="fake-key", enabled=True)
        original = "佛尔摩斯蹲下身审视太武士河边的钢国死尸。" * 5
        fixed = "福尔摩斯蹲下身审视泰晤士河边的刚果死尸。" * 5

        with mock.patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock.MagicMock()
            mock_client.chat.completions.create.return_value = _make_response(fixed)
            mock_openai_cls.return_value = mock_client

            text, status = proc.post_process(
                original,
                context={"title": "侦探小说历史", "kind": "douyin"},
            )

        self.assertEqual(status, STATUS_REVIEWED)
        self.assertIn("福尔摩斯", text)
        self.assertNotIn("佛尔摩斯", text)
        # 验证 API 调用参数
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "deepseek-chat")
        self.assertEqual(call_kwargs["temperature"], 0.0)
        # 验证 prompt 含视频标题
        user_msg = call_kwargs["messages"][1]["content"]
        self.assertIn("侦探小说历史", user_msg)
        self.assertIn("佛尔摩斯", user_msg)  # 原文进 prompt

    def test_strips_code_fences_from_response(self):
        """模型可能输出 ```markdown ... ``` 包裹,应剥离。"""
        from mediascribe.llm_post_process import STATUS_REVIEWED, LLMPostProcessor
        proc = LLMPostProcessor(api_key="fake-key", enabled=True)
        original = "原始 ASR 文本需要修正的内容。" * 5
        fixed_content = "修正后的 ASR 文本内容。" * 5
        # 模拟模型用代码块包裹输出
        wrapped = f"```markdown\n{fixed_content}\n```"

        with mock.patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock.MagicMock()
            mock_client.chat.completions.create.return_value = _make_response(wrapped)
            mock_openai_cls.return_value = mock_client

            text, status = proc.post_process(original)

        self.assertEqual(status, STATUS_REVIEWED)
        self.assertNotIn("```", text)
        self.assertIn("修正后的", text)


class TestLLMPostProcessorFailed(unittest.TestCase):
    """API 抛错 → 返回原文 + llm-failed,不阻塞。"""

    def test_api_exception_returns_failed_and_original_text(self):
        from mediascribe.llm_post_process import STATUS_FAILED, LLMPostProcessor
        proc = LLMPostProcessor(api_key="fake-key", enabled=True)
        original = "原始 ASR 文本需要修正的内容。" * 5

        with mock.patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock.MagicMock()
            mock_client.chat.completions.create.side_effect = RuntimeError("API timeout")
            mock_openai_cls.return_value = mock_client

            text, status = proc.post_process(original)

        self.assertEqual(status, STATUS_FAILED)
        # 失败时返回原文,不抛错
        self.assertEqual(text, original)

    def test_empty_api_response_returns_failed(self):
        from mediascribe.llm_post_process import STATUS_FAILED, LLMPostProcessor
        proc = LLMPostProcessor(api_key="fake-key", enabled=True)
        original = "原始 ASR 文本需要修正的内容。" * 5

        with mock.patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock.MagicMock()
            mock_client.chat.completions.create.return_value = _make_response("")
            mock_openai_cls.return_value = mock_client

            text, status = proc.post_process(original)

        self.assertEqual(status, STATUS_FAILED)
        self.assertEqual(text, original)


class TestLLMPostProcessorFromEnv(unittest.TestCase):
    """from_env / from_settings 构造正确。"""

    def test_from_env_reads_all_vars(self):
        from mediascribe.llm_post_process import LLMPostProcessor
        env = {
            "MEDIASCRIBE_LLM_API_KEY": "env-key-123",
            "MEDIASCRIBE_LLM_API_BASE": "https://api.example.com",
            "MEDIASCRIBE_LLM_MODEL": "gpt-4o-mini",
            "MEDIASCRIBE_LLM_ENABLED": "true",
            "MEDIASCRIBE_LLM_TIMEOUT": "60",
            "MEDIASCRIBE_LLM_MAX_CHARS": "8000",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            proc = LLMPostProcessor.from_env()
        self.assertEqual(proc.api_key, "env-key-123")
        self.assertEqual(proc.api_base, "https://api.example.com")
        self.assertEqual(proc.model, "gpt-4o-mini")
        self.assertTrue(proc.enabled)
        self.assertEqual(proc.timeout, 60.0)
        self.assertEqual(proc.max_chars, 8000)

    def test_from_env_disabled_by_default(self):
        """无 api_key 时 enabled=False。"""
        from mediascribe.llm_post_process import LLMPostProcessor
        # 清空所有相关环境变量
        env_keys = [
            "MEDIASCRIBE_LLM_API_KEY",
            "MEDIASCRIBE_LLM_API_BASE",
            "MEDIASCRIBE_LLM_MODEL",
            "MEDIASCRIBE_LLM_ENABLED",
            "MEDIASCRIBE_LLM_TIMEOUT",
            "MEDIASCRIBE_LLM_MAX_CHARS",
        ]
        clean_env = dict.fromkeys(env_keys, "")
        with mock.patch.dict("os.environ", clean_env, clear=False):
            # patch os.environ.get to return "" for our keys
            with mock.patch("os.environ.get") as mock_get:
                def side_effect(key, default=""):
                    if key in env_keys:
                        return ""
                    return default
                mock_get.side_effect = side_effect
                proc = LLMPostProcessor.from_env()
        self.assertFalse(proc.enabled)

    def test_from_settings_namespace(self):
        """Settings 有 llm_post_process dict 字段时优先用。"""
        from types import SimpleNamespace

        from mediascribe.llm_post_process import LLMPostProcessor

        settings = SimpleNamespace(
            llm_post_process={
                "api_key": "dict-key",
                "api_base": "https://custom.api.com",
                "model": "custom-model",
                "enabled": True,
            }
        )
        proc = LLMPostProcessor.from_settings(settings)
        self.assertIsNotNone(proc)
        assert proc is not None  # for type checker
        self.assertEqual(proc.api_key, "dict-key")
        self.assertEqual(proc.api_base, "https://custom.api.com")
        self.assertEqual(proc.model, "custom-model")
        self.assertTrue(proc.enabled)

    def test_from_settings_falls_back_to_env(self):
        from types import SimpleNamespace

        from mediascribe.llm_post_process import LLMPostProcessor

        settings = SimpleNamespace(llm_post_process=None)
        env = {"MEDIASCRIBE_LLM_API_KEY": "fallback-key", "MEDIASCRIBE_LLM_ENABLED": "1"}
        with mock.patch.dict("os.environ", env, clear=False):
            proc = LLMPostProcessor.from_settings(settings)
        self.assertIsNotNone(proc)
        assert proc is not None
        self.assertEqual(proc.api_key, "fallback-key")
        self.assertTrue(proc.enabled)


class TestBuildStatusBanner(unittest.TestCase):
    """banner 生成正确。"""

    def test_reviewed_with_model(self):
        from mediascribe.llm_post_process import STATUS_REVIEWED, build_status_banner
        banner = build_status_banner(STATUS_REVIEWED, "deepseek-chat")
        self.assertEqual(
            banner,
            "<!-- post-process: llm-reviewed (model=deepseek-chat) -->",
        )

    def test_disabled_banner(self):
        from mediascribe.llm_post_process import STATUS_DISABLED, build_status_banner
        banner = build_status_banner(STATUS_DISABLED)
        self.assertIn("llm-disabled", banner)
        self.assertIn("not LLM-reviewed", banner)

    def test_failed_banner(self):
        from mediascribe.llm_post_process import STATUS_FAILED, build_status_banner
        banner = build_status_banner(STATUS_FAILED)
        self.assertIn("llm-failed", banner)
        self.assertIn("fell back", banner)


class TestAssembleStageIntegration(unittest.TestCase):
    """AssembleStage 集成测试: LLM 后处理 + banner 注入。"""

    def test_disabled_llm_injects_disabled_banner(self):
        """无 api_key → banner 显示 llm-disabled,文本不变。"""
        from mediascribe.pipeline_stages import AssembleStage

        # 构造最小 stage (helper 函数用 stub)
        def resolve_output(name, output):
            return Path("/tmp/test.md")

        def resolve_meta(transcript_path):
            return Path("/tmp/test.json")

        def build_md(*args, **kwargs):
            return "原文内容"

        stage = AssembleStage(resolve_output, resolve_meta, build_md)

        # 直接测 _inject_status_banner 静态方法
        result = stage._inject_status_banner("原文", "llm-disabled", "")
        self.assertTrue(result.startswith("<!-- post-process: llm-disabled"))
        self.assertIn("原文", result)

    def test_reviewed_llm_injects_reviewed_banner(self):
        from mediascribe.pipeline_stages import AssembleStage
        stage = AssembleStage(lambda *a: None, lambda *a: None, lambda *a: "")
        result = stage._inject_status_banner("修正后", "llm-reviewed", "deepseek-chat")
        self.assertIn("llm-reviewed", result)
        self.assertIn("model=deepseek-chat", result)


if __name__ == "__main__":
    unittest.main()
