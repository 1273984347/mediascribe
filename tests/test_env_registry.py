"""R-4(2026-11-21 架构评审): env 单一真源一致性 —— 代码 / 注册表 / 文档三方钉死。

* 源码里 grep 到的每个 ``MEDIASCRIBE_*`` / ``DOUYIN_BATCH_*`` /
  ``DOYIN_BATCH_*`` 变量名必须在 ``env_registry.ENV_VARS``(+legacy 展开)里;
* 注册表里的每个正名必须出现在 ``docs/configuration.md``;
* legacy 前缀展开与 :func:`env_registry.canonical_name` 一致。
"""

from __future__ import annotations

import re
from pathlib import Path

from mediascribe.env_registry import (
    BATCH_SUFFIXES,
    ENV_VARS,
    LEGACY_PREFIXES,
    canonical_name,
    describe,
    iter_all_names,
)

PKG_DIR = Path(__file__).resolve().parent.parent / "mediascribe"
DOCS_PAGE = Path(__file__).resolve().parent.parent / "docs" / "configuration.md"

_ENV_NAME_RE = re.compile(r"MEDIASCRIBE_[A-Z0-9_]+|DOUYIN_BATCH_[A-Z0-9_]+|DOYIN_BATCH_[A-Z0-9_]+")


class TestEnvRegistryConsistency:
    def test_every_name_in_source_is_registered(self):
        found: set[str] = set()
        for py in PKG_DIR.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            found |= set(_ENV_NAME_RE.findall(text))
        # 裸前缀占位(如 docstring 里的 "MEDIASCRIBE_LLM_")不算变量名
        found = {n for n in found if not n.endswith("_")}
        registered = set(iter_all_names()) | set(ENV_VARS)
        missing = sorted(found - registered)
        assert not missing, f"源码使用了注册表之外的环境变量(补进 env_registry.ENV_VARS): {missing}"

    def test_every_registered_name_is_documented(self):
        doc = DOCS_PAGE.read_text(encoding="utf-8")
        for name in ENV_VARS:
            assert f"`{name}`" in doc, f"{name} 未出现在 docs/configuration.md(重新生成该页)"

    def test_legacy_prefixes_expand_to_canonical(self):
        for prefix in LEGACY_PREFIXES:
            for suffix in BATCH_SUFFIXES:
                assert canonical_name(prefix + suffix) == "MEDIASCRIBE_BATCH_" + suffix

    def test_describe_falls_back_to_en_for_unknown_lang(self):
        assert (
            describe("MEDIASCRIBE_WORKSPACE", lang="xx")
            == ENV_VARS["MEDIASCRIBE_WORKSPACE"]["desc"]["en"]
        )

    def test_registry_covers_batch_suffixes(self):
        for suffix in BATCH_SUFFIXES:
            assert f"MEDIASCRIBE_BATCH_{suffix}" in ENV_VARS
