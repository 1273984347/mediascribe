# AGENTS.md — MediaScribe 仓库指南（面向 AI Agent）

> **MediaScribe** 是**完全离线**的视频转文字工具：从 B站 / 抖音 / YouTube / 小红书 /
> 微信公众号 / 本地文件取视频，用本地 Whisper 系模型转成 Markdown。无云服务、无 API Key、
> 数据不出本机。
>
> 本文件是 Agent 在本仓库的**权威指引**。`.github/copilot-instructions.md` 只是它的指针。

---

## 一、仓库地图

```
mediascribe/          # 唯一产品包（安装名 mediascribe）
├── __main__.py       # python -m mediascribe 的子命令分发
├── pipeline*.py      # 转录主流程（pipeline / pipeline_async / pipeline_stages）
├── transcribers/     # ASR 后端（whisper / whisperx / faster_whisper / whisper_cpp / chunked）
├── downloaders/      # 平台下载器（douyin / wechat_mp / xiaohongshu / youtube / ytdlp）
├── plugins/          # 插件注册表（entry_points 发现）
├── web/              # FastAPI Web UI（app.py 是门面，实际逻辑在 assembly.py /
│                     #   route_*.py / validation.py / models.py / job_runner.py + static/ ES modules）
├── config.py models.py url_utils.py inputs.py ...
├── i18n.py           # 双语消息表（en / zh）—— 所有面向用户的字符串走 t()
│                     #   （tests/test_i18n_guard.py 用 AST 门禁强制；存量基线只减不增）
├── security.py       # URL / 文件名安全（is_safe_url / sanitize_filename / safe_join_path /
│                     #   TRUSTED_DOMAINS / check_url_safety —— 平台域名一律进白名单表，勿散落硬编码）
├── platform_compat.py# 跨平台助手（ffmpeg 探测 / safe_filename / 路径规范化）
├── batch_*.py        # 批量归档编排（batch_cli / batch_config / batch_cache）
├── agent_output.py   # --json 输出 schema（mediascribe.agent-output/v1）
└── ...

tests/               # 单测（pytest）
docs/                # mkdocs 站点源（docs_dir: docs）+ reports/ 历史报告
extension/           # 浏览器扩展（被 mediascribe/web/route_extension.py + extension_builder.py 使用）
examples/plugins/    # 第三方插件样例（vimeo_downloader / tcn_transformer）
scripts/             # 开发脚本，按职责分层（见下）
```

> ⚠️ **2026-10-24 结构变更**：旧的 `douyin_batch/` 包与根级 `douyin_batch_v3.py` 已**整体并入**
> `mediascribe/`。旧的 `douyin_batch.cache` / `.config` / `.progress` 转发壳已删除，对应实现是
> `mediascribe.batch_cache` / `mediascribe.batch_config` / `mediascribe.progress`。
> 若你看到引用 `douyin_batch` 的代码或文档，那是**待修的漂移**，不是现状。

### scripts/ 分层

| 目录 | 用途 |
|---|---|
| `scripts/bench/` | 基准：L1 前端框架 / L2 工作台壳 / L3 ASR 引擎 / `corpus/` 语料生成 |
| `scripts/e2e/` | 真实网络 E2E 与 CLI help 快照采集 |
| `scripts/pipeline/` | 批量转录与术语修订管线（batch_transcribe / fix_xutao_terms / seed_learned_terms） |
| `scripts/tools/` | 辅助工具（抖音下载 / 抓取 / URL 解析 / txt→md 转换） |
| `scripts/launch/` | 一键启动器（`one_click_up.py` + `.sh` / `.ps1` 薄包装） |
| `scripts/gates/` | 门禁（`pre_push_gate.py`：ruff + 快速测试） |

顶层两个入口：`scripts/run_tests.py`（pytest 转发）、`scripts/demo_v3.py`（功能演示）。

---

## 二、常用命令

```bash
# 安装
pip install -r requirements.txt
pip install -r requirements-dev.txt      # 开发依赖（pytest / ruff / mypy / bandit）

# 测试（与 CI unit job 同参数）
python scripts/run_tests.py
python -m pytest -m "not integration and not network" --ignore=tests/test_e2e_real_urls.py -q

# 门禁（pre-push 必跑：lint + 快速测试）
python scripts/gates/pre_push_gate.py
make gate          # 同上（GNU Make）
just gate          # 同上（just）

# Lint / 格式化（ruff 是唯一事实源，不是 black/flake8）
python -m ruff check .
python -m ruff format --check .
python -m ruff format .

# 类型检查（CI 阻断门禁）
mypy mediascribe

# 文档站
mkdocs build --strict      # docs_dir: docs

# CLI
python -m mediascribe --help
python -m mediascribe doctor            # 环境自检
python -m mediascribe transcribe "<URL>"
python -m mediascribe archive --user "<作者主页URL>" -n 20   # 批量归档
```

---

## 三、开发约定

- **路径**：一律用 `pathlib.Path`，不要拼字符串路径。
- **类型**：公共函数必须有类型标注；**docstring 用英文**（2026-10-29 起恢复执行：
  当日新增的 7 个模块已全量转英文；存量老模块仍为中文，改动到哪篇就顺手转哪篇）。
- **用户可见字符串**：**必须**走 `mediascribe/i18n.py` 的 `t()`；新增 key 时在 `Messages`
  类里写成 `{"en": ..., "zh": ...}` 字典。**有 AST 门禁强制**：`tests/test_i18n_guard.py`
  扫描全包 `print`/`raise` 里的 CJK 字面量，基线外的直接红灯（2026-10-29 起执行；
  基线 = `test_i18n_guard.py` 里的 `_BASELINE`，只允许缩小）。argparse help 文本走
  `build_parser(i18n_t)` 模式（见 batch_cli），暂不在 AST 门禁范围内。
- **URL 安全**：外部 URL 的**域名白名单**用 `mediascribe.security.check_url_safety()` /
  `TRUSTED_DOMAINS`（与 `url_utils._PLATFORM_FINGERPRINTS` 的一致性由
  `tests/test_platform_consistency.py` 钉死）；`is_safe_url()` 只是格式检查（不查域名），
  单独用它不构成安全判断。平台识别按主机名匹配（`url_utils.match_platform`），下载器
  入口必须再做一次主机名断言，cookies 只准发对应平台域名。
- **文件名安全**：一切用户可控文件名必须过 `mediascribe.security.sanitize_filename()`。
- **平台常量**：不要在业务逻辑里硬编码平台名/域名，用 `mediascribe.security.TRUSTED_DOMAINS`。
- **不要引入新顶层依赖**：同时更新 `pyproject.toml` 与 `requirements.txt`。
- **Python 语法**：≥ 3.11，可用 `X | Y` / `list[int]`，无需 `from __future__ import annotations`。

---

## 四、硬性规则

1. **绝不提交**：`output/`、`models/`、`*.cookies`、`cookies.txt`、`.env`、`my_config.json`、
   `config.json`（本地真实副本）。
2. **绝不提交** `site/`（mkdocs 构建产物）、`archive/`、`web-workspace/`、各类 `__pycache__`。
3. **改文档就要同步改实现**：本仓库有历史教训 —— `docs/benchmarking.md` 曾长期描述**根本不存在的
   CLI 参数**（`--real` / `--audio-path` / `--engine`），误导用户。改脚本必须同步改对应文档页。
4. **改枚举/常量要查全**：同一个名字常在多处硬编码（config / CLI choices / web 层校验正则 /
   factory 回退链）。改完必须跑 `tests/test_engine_consistency.py` 这类一致性测试。
5. **提交前跑门禁**：`python scripts/gates/pre_push_gate.py`。它会跑 ruff 与 CI 同参快速测试，
   任何失败非零退出。

---

## 五、常见任务配方

| 任务 | 落点 | 必跑 |
|---|---|---|
| 加一个下载平台 | `mediascribe/downloaders/` + `mediascribe/url_utils.py` 的平台路由 | `tests/test_new_downloaders.py` |
| 加一个 ASR 引擎 | `mediascribe/transcribers/` + `factory.py` + `config.py::_VALID_ENGINES` | `tests/test_engine_consistency.py` |
| 改 Web UI | `mediascribe/web/` 按职责落点：路由 `route_*.py`、请求校验 `validation.py` + `models.py`、装配 `assembly.py`、页面 `static/` | `tests/test_web_app*.py` |
| 改批处理编排 | `mediascribe/batch_cli.py` + `batch_config.py` | `tests/test_review_fixes.py` |
| 改 CLI 输出 | `mediascribe/agent_output.py`（**schema 升版需谨慎**） | `tests/test_agent_output.py` |
| 改文档站 | `docs/` + `mkdocs.yml` 的 nav | `mkdocs build --strict` |

---

## 六、别做的事

- ❌ 不要新建 `douyin_batch/` 下的任何东西 —— 那个包已不存在。
- ❌ 不要在 `docs/` 下新增页面却不同步 `mkdocs.yml` 的 `nav` —— CI 用 `--strict` 构建，
  未列入 nav 的页面会**直接让流水线变红**。
- ❌ 不要手改 `mediascribe.agent-output/v1` 的字段值（如 `command`）—— 那是**稳定 schema
  标识**，下游 Agent 会依赖；要改必须升 schema 版本。
- ❌ 不要把 `output/` 或 `archive/` 重新加进版本控制。
- ❌ 不要用 `black` / `flake8` —— 本仓库的 lint 事实源是 **ruff**。
