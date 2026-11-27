"""``python -m mediascribe doctor`` — 环境自检 (v3.4.0)

一次跑完新用户最容易踩的所有坑，逐项给出 ✅ / ⚠️ / ❌ 和修复提示：

* Python 版本
* ffmpeg / ffprobe（转录硬依赖，缺失直接 fail）
* 运行设备（CUDA / Metal / ROCm / CPU）与显存
* 四个转录引擎的安装状态（whisper / faster-whisper / whisperx / whisper-cpp）
* 转录模型磁盘缓存（已下载哪些模型）与**权重预置完整性**（离线必需）
* 工作区目录（默认 ``./output``）可写性
* 微信公众号 cookies（env / 文件）
* 抖音 cookies 文件（``douyin_cookies.txt``）
* ASR 术语库（``learn`` 累积的条数）

退出码：READY / READY_WITH_WARNINGS 返回 0，BLOCKED 返回 1。
BLOCKED 包括 Python 版本过低、缺 ffmpeg、无可用 ASR 引擎、离线模式缺模型、
工作区不可写；其余缺项按警告展示。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from .i18n import t

__all__ = ["run_doctor"]

_OK, _WARN, _FAIL = "✅", "⚠️ ", "❌"


def _check(name: str, ok: bool, detail: str = "", hint: str = "") -> bool:
    icon = _OK if ok else _WARN
    line = f"  {icon} {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if hint and not ok:
        print(f"      💡 {hint}")
    return ok


def _engine_available(module: str) -> tuple[bool, str]:
    spec = importlib.util.find_spec(module)
    if spec is None:
        return False, "未安装"
    return True, "已安装"


def _model_cache_dir() -> Optional[Path]:
    for env, _default in (
        ("MEDIASCRIBE_CACHE_DIR", None),
        ("XDG_CACHE_HOME", None),
    ):
        raw = os.environ.get(env, "").strip()
        if raw:
            return Path(raw) / "mediascribe" if env == "XDG_CACHE_HOME" else Path(raw)
    return Path.home() / ".cache" / "mediascribe"


def _list_downloaded_models(cache_dir: Optional[Path]) -> List[str]:
    if cache_dir is None or not cache_dir.exists():
        return []
    return sorted(p.name for p in cache_dir.iterdir() if p.is_dir() and not p.name.startswith("."))


def _check_python() -> bool:
    """📌 Python — 版本下限 3.11。"""
    print("\n📌 Python")
    v = sys.version_info
    return _check(
        "版本",
        v >= (3, 11),
        f"{v.major}.{v.minor}.{v.micro}",
        "需要 Python 3.11+",
    )


def _check_av_tools() -> bool:
    """📌 音视频工具 — ffmpeg 是转录硬依赖, 缺失即关键失败。

    返回是否关键依赖就绪(供 run_doctor 决定退出码)。
    """
    print(t("DOCTOR_SECTION_TOOLS"))
    ffmpeg = shutil.which("ffmpeg")
    critical_ok = _check(
        "ffmpeg",
        ffmpeg is not None,
        ffmpeg or "未在 PATH 中找到",
        "安装 ffmpeg 并加入 PATH（https://ffmpeg.org），转录硬依赖",
    )
    ffprobe = shutil.which("ffprobe")
    _check(
        "ffprobe",
        ffprobe is not None,
        ffprobe or "未找到（--model auto 的时长探测会退回 small）",
        "随 ffmpeg 一起安装",
    )
    return critical_ok


def _check_device() -> None:
    """📌 运行设备 — CUDA / Metal / ROCm / CPU 探测。"""
    print(t("DOCTOR_SECTION_DEVICE"))
    try:
        from .pipeline import gpu_health, resolve_device

        device = resolve_device("auto")
        health = gpu_health()
        if health.get("available"):
            _check(
                "GPU 加速",
                True,
                f"{device} · {health.get('name')}"
                + (
                    f" · 显存 {health.get('vram_used_mb')}/{health.get('vram_total_mb')} MB"
                    if health.get("vram_total_mb")
                    else ""
                ),
            )
        else:
            # 2026-11-03 首航自测: 区分「没有 N 卡」与「有 N 卡但 torch 是
            # CPU 版」— 后者给精确修复命令(PyPI 的 torch 默认不带 CUDA,
            # pip install torch 装完即 CPU 版, 用户无从得知)
            has_nvidia = shutil.which("nvidia-smi") is not None
            if has_nvidia:
                hint = (
                    "检测到 NVIDIA 显卡但当前 torch 为 CPU 版 — 执行: "
                    "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124"
                )
            else:
                hint = "NVIDIA 显卡安装 CUDA 版 PyTorch 可提速数倍"
            _check(
                "GPU 加速",
                False,
                "不可用，将使用 CPU（可用但较慢）",
                hint,
            )
    except Exception as exc:  # pragma: no cover - 探测自身异常
        _check("设备探测", False, f"异常: {exc!r}")


def _check_gpu_budget() -> None:
    """R-3(2026-11-21 架构评审): 显存账本闸门状态可视 —— 闸门未启用必须可诊断。

    附带触发一次预算探测(缓存)并给出账本快照;跨进程边界(Web 与 MCP
    不互斥)是已声明的单机产品边界,见 gpu_budget 模块 docstring。
    """
    try:
        from .gpu_budget import budget_total_mb, get_vram_ledger

        budget_total_mb()  # 触发探测并缓存(失败也有 warn 日志)
        snap = get_vram_ledger().snapshot()
        if snap.get("enabled"):
            _check(
                "显存账本闸门",
                True,
                t(
                    "DOCTOR_GPU_BUDGET_ON",
                    total=snap.get("total_mb"),
                    available=snap.get("available_mb"),
                ),
            )
        else:
            _check("显存账本闸门", False, t("DOCTOR_GPU_BUDGET_OFF"), "")
    except Exception as exc:  # pragma: no cover - defensive
        _check("显存账本闸门", False, f"探测异常: {exc!r}", "")


def _check_engines() -> tuple[Optional[str], str]:
    """📌 转录引擎 — 安装状态与统一自动推荐。"""
    from .config import EngineUnavailableError, resolve_default_engine

    print(t("DOCTOR_SECTION_ENGINES"))
    engines = [
        ("whisper", "whisper（兼容回退）", 'pip install "mediascribe[whisper]"'),
        ("faster_whisper", "faster-whisper（推荐）", 'pip install "mediascribe[faster-whisper]"'),
        ("whisperx", "whisperx（说话人分离/对齐）", 'pip install "mediascribe[whisperx]"'),
    ]
    for module, label, hint in engines:
        avail, detail = _engine_available(module)
        _check(label, avail, detail, hint)

    try:
        engine, notice = resolve_default_engine()
    except EngineUnavailableError as exc:
        _check("自动选择", False, str(exc), "安装推荐引擎后重新运行 doctor")
        return None, ""
    detail = f"{engine} + small"
    if notice:
        detail += f" — {notice}"
    _check("自动选择", True, detail)
    return engine, notice


def _check_model_cache() -> None:
    """📌 模型缓存 — 已下载哪些转录模型。"""
    print(t("DOCTOR_SECTION_MODELS"))
    cache_dir = _model_cache_dir()
    models = _list_downloaded_models(cache_dir)
    if models:
        _check("缓存目录", True, f"{cache_dir}（{len(models)} 项: {', '.join(models[:8])}）")
    else:
        _check(
            "缓存目录",
            True,
            f"{cache_dir}（{t('DOCTOR_MODEL_CACHE_EMPTY')}）",
        )


def _check_model_weights(engine: Optional[str] = None) -> tuple[bool, bool]:
    """📌 权重预置 — 离线产品必须**预先放置**的模型权重。

    此前缺权重只能在跑到一半时炸（2026-10-23 实测：``faster-whisper medium``
    在本机抛 ``LocalEntryNotFoundError``，因为只缓存了 small / large-v3）。
    现在按引擎列出各档位的就绪情况，并给出预置指引。
    """
    print(t("DOCTOR_SECTION_WEIGHTS"))
    try:
        from .model_weights import check_weights, known_weight_pairs
    except Exception as exc:  # pragma: no cover - 模块缺失不应毁掉整个自检
        _check("权重清单", False, f"无法加载 model_weights: {exc!r}")
        return False, False

    by_engine: dict = {}
    for engine, model in known_weight_pairs():
        by_engine.setdefault(engine, []).append(check_weights(engine, model))

    # 2026-11-03 首航自测: 未安装的引擎不列权重(whisperx 没装却列 8 档
    # missing 纯属噪音); 部分预置的缺失折叠成一行而非逐档刷屏
    _ENGINE_MODULES = {
        "whisper": "whisper",
        "faster_whisper": "faster_whisper",
        "whisperx": "whisperx",
    }
    for engine, statuses in by_engine.items():
        module = _ENGINE_MODULES.get(engine)
        if module is not None and importlib.util.find_spec(module) is None:
            print(t("DOCTOR_ENGINE_SKIP", engine=engine))
            continue
        ready = [s.model for s in statuses if s.ok]
        missing = [s for s in statuses if not s.ok]
        if ready and not missing:
            _check(
                f"{engine} 权重",
                True,
                f"已预置 {len(ready)}/{len(statuses)} 档: {', '.join(ready)}",
            )
        elif ready:
            _check(
                f"{engine} 权重",
                True,
                f"已预置 {len(ready)}/{len(statuses)} 档: {', '.join(ready)}",
                f"缺 {len(missing)} 档: {', '.join(s.model for s in missing)}"
                f"（应位于 {missing[0].location}）",
            )
        else:
            _check(
                f"{engine} 权重",
                False,
                f"0/{len(statuses)} 档已预置（应位于 {missing[0].location}）",
                missing[0].hint,
            )

    if engine is None:
        return False, False
    recommended = check_weights(engine, "small")
    return recommended.ok, not recommended.ok


def _check_workspace(workspace: Optional[Path]) -> bool:
    """📌 工作区 — 目录存在且可写(写探针实测)。"""
    print(t("DOCTOR_SECTION_WORKSPACE"))
    ws = (
        Path(workspace)
        if workspace
        else Path(os.environ.get("MEDIASCRIBE_WORKSPACE", "").strip() or Path.cwd() / "output")
    )
    try:
        ws.mkdir(parents=True, exist_ok=True)
        probe = ws / ".doctor_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        _check("可写", True, str(ws.resolve()))
        return True
    except Exception as exc:
        _check("可写", False, f"{ws} — {exc!r}", "检查目录权限或用 --workspace 换路径")
        return False


def _check_cookies() -> None:
    """📌 Cookies — 微信公众号 / 抖音的可选登录凭据。"""
    print(t("DOCTOR_SECTION_COOKIES"))
    wechat_env = os.environ.get("MEDIASCRIBE_WECHAT_COOKIE", "").strip()
    wechat_local = [p for p in (Path("wechat_cookies.json"),) if p.exists()]
    _check(
        "微信公众号 cookies",
        bool(wechat_env or wechat_local),
        "已配置" if (wechat_env or wechat_local) else "未配置（登录墙文章会失败）",
        "transcribe 时传 --wechat-cookie-file，或设 MEDIASCRIBE_WECHAT_COOKIE",
    )
    douyin_cookie = None
    for cand in ("douyin_cookies.txt", "cookies.txt"):
        if Path(cand).exists():
            douyin_cookie = cand
            break
    _check(
        "抖音 cookies",
        douyin_cookie is not None,
        douyin_cookie or "未找到（部分视频下载需要）",
        "浏览器导出 cookies.txt 放到项目根目录（见 docs/ 平台文档）",
    )


def _check_terms() -> None:
    """📌 ASR 术语库 — learn 累积的条数(读不到按空处理, 不算失败)。"""
    print(t("DOCTOR_SECTION_TERMS"))
    try:
        from .learn import _load_db  # type: ignore[attr-defined]

        stats = _load_db().stats()
        total = stats.get("total", 0)
        _check(
            "learn 术语",
            True,
            f"{total} 条（{stats.get('active', 0)} 条生效）"
            if total
            else "空（python -m mediascribe learn edit 可积累）",
        )
    except Exception:
        _check("learn 术语", True, "无法读取（跳过）")


def _check_env_config() -> None:
    """R-4(2026-11-21 架构评审): env 变量汇总 —— 单一真源 env_registry。

    只列**已设置**的变量(全部展开 50+ 行太吵),敏感变量一律打码;
    附带 R-2 的无鉴权告警。
    """
    from .env_registry import ENV_VARS, current_value

    print(t("DOCTOR_SECTION_ENV"))
    set_any = False
    for name in sorted(ENV_VARS):
        val = current_value(name)
        if val == "(unset)":
            continue
        set_any = True
        _check(name, True, val)
    if not set_any:
        _check("MEDIASCRIBE_*", True, "(none set — all defaults)")
    if not os.environ.get("MEDIASCRIBE_API_TOKEN", "").strip():
        print(f"  {_WARN} {t('DOCTOR_ENV_TOKEN_HINT')}")


def run_doctor(workspace: Optional[Path] = None, *, offline: bool = False) -> int:
    """执行环境自检并返回进程退出码。

    READY / READY_WITH_WARNINGS 返回 0；BLOCKED 返回 1。
    ``offline=True`` 表示用户明确声明当前离线，此时缺失模型是阻塞项。
    """
    print(t("DOCTOR_BANNER"))
    print("=" * 50)

    blockers: List[str] = []
    warnings: List[str] = []

    if not _check_python():
        blockers.append("PYTHON_VERSION")
    if not _check_av_tools():
        blockers.append("FFMPEG_MISSING")
    _check_device()
    _check_gpu_budget()
    engine, engine_notice = _check_engines()
    if engine is None:
        blockers.append("ENGINE_UNAVAILABLE")
    elif engine_notice:
        warnings.append("ENGINE_FALLBACK_WHISPER")
    _check_model_cache()
    _, model_missing = _check_model_weights(engine)
    if model_missing:
        if offline:
            blockers.append("MODEL_NOT_READY")
        else:
            warnings.append("MODEL_NOT_READY")
    if not _check_workspace(workspace):
        blockers.append("OUTPUT_WRITE_FAILED")
    from .events import event_retention_status

    retention = event_retention_status()
    if retention["warning"]:
        warnings.append(retention["warning"])
    _check_cookies()
    _check_terms()
    _check_env_config()

    status = "BLOCKED" if blockers else ("READY_WITH_WARNINGS" if warnings else "READY")
    try:
        from .events import EventRecorder

        EventRecorder(surface="cli").record_readiness(
            status=status,
            exit_code=1 if blockers else 0,
            blockers=blockers,
            warnings=warnings,
            engine=engine,
            force=True,
        )
    except Exception:  # pragma: no cover - telemetry must never alter doctor
        import logging

        logging.getLogger(__name__).debug("event readiness recording failed", exc_info=True)
    print("\n" + "=" * 50)
    print(
        t(
            "DOCTOR_STATUS",
            status=status,
            blockers=", ".join(blockers) or "-",
            warnings=", ".join(warnings) or "-",
        )
    )
    if blockers:
        print(t("DOCTOR_CRITICAL_MISSING"))
        return 1
    if warnings:
        print(t("DOCTOR_READY_WITH_WARNINGS"))
        return 0
    print(t("DOCTOR_ALL_OK"))
    return 0
