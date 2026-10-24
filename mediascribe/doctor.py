"""``python -m mediascribe doctor`` — 环境自检 (v3.4.0)

一次跑完新用户最容易踩的所有坑，逐项给出 ✅ / ⚠️ / ❌ 和修复提示：

* Python 版本
* ffmpeg / ffprobe（转录硬依赖，缺失直接 fail）
* 运行设备（CUDA / Metal / ROCm / CPU）与显存
* 三个转录引擎的安装状态（whisper / faster-whisper / whisperx）
* 转录模型磁盘缓存（已下载哪些模型）
* 工作区目录（默认 ``./output``）可写性
* 微信公众号 cookies（env / 文件）
* 抖音 cookies 文件（``douyin_cookies.txt``）
* ASR 术语库（``learn`` 累积的条数）

退出码：ffmpeg 缺失返回 1，其余情况一律 0 — doctor 的定位是
"诊断报告"，非关键项缺失不应阻断脚本调用方。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

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


def _check_python() -> None:
    """📌 Python — 版本下限 3.11。"""
    print("\n📌 Python")
    v = sys.version_info
    _check(
        "版本",
        v >= (3, 11),
        f"{v.major}.{v.minor}.{v.micro}",
        "需要 Python 3.11+",
    )


def _check_av_tools() -> bool:
    """📌 音视频工具 — ffmpeg 是转录硬依赖, 缺失即关键失败。

    返回是否关键依赖就绪(供 run_doctor 决定退出码)。
    """
    print("\n📌 音视频工具")
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
    print("\n📌 运行设备")
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
            _check(
                "GPU 加速",
                False,
                "不可用，将使用 CPU（可用但较慢）",
                "NVIDIA 显卡安装 CUDA 版 PyTorch 可提速数倍",
            )
    except Exception as exc:  # pragma: no cover - 探测自身异常
        _check("设备探测", False, f"异常: {exc!r}")


def _check_engines() -> None:
    """📌 转录引擎 — 三个可选引擎的安装状态。"""
    print("\n📌 转录引擎")
    engines = [
        ("whisper", "whisper（默认）", "pip install -r requirements.txt"),
        ("faster_whisper", "faster-whisper（更快）", "pip install faster-whisper"),
        ("whisperx", "whisperx（说话人分离/对齐）", "pip install whisperx"),
    ]
    for module, label, hint in engines:
        avail, detail = _engine_available(module)
        _check(label, avail, detail, hint)


def _check_model_cache() -> None:
    """📌 模型缓存 — 已下载哪些转录模型。"""
    print("\n📌 模型缓存")
    cache_dir = _model_cache_dir()
    models = _list_downloaded_models(cache_dir)
    if models:
        _check("缓存目录", True, f"{cache_dir}（{len(models)} 项: {', '.join(models[:8])}）")
    else:
        _check(
            "缓存目录",
            True,
            f"{cache_dir}（空 — 首次转录时会自动下载模型）",
        )


def _check_model_weights() -> None:
    """📌 权重预置 — 离线产品必须**预先放置**的模型权重。

    此前缺权重只能在跑到一半时炸（2026-10-23 实测：``faster-whisper medium``
    在本机抛 ``LocalEntryNotFoundError``，因为只缓存了 small / large-v3）。
    现在按引擎列出各档位的就绪情况，并给出预置指引。
    """
    print("\n📌 权重预置（离线必需）")
    try:
        from .model_weights import check_weights, known_weight_pairs
    except Exception as exc:  # pragma: no cover - 模块缺失不应毁掉整个自检
        _check("权重清单", False, f"无法加载 model_weights: {exc!r}")
        return

    by_engine: dict = {}
    for engine, model in known_weight_pairs():
        by_engine.setdefault(engine, []).append(check_weights(engine, model))

    for engine, statuses in by_engine.items():
        ready = [s.model for s in statuses if s.ok]
        missing = [s for s in statuses if not s.ok]
        if ready:
            _check(
                f"{engine} 权重",
                True,
                f"已预置 {len(ready)}/{len(statuses)} 档: {', '.join(ready)}",
            )
        else:
            _check(
                f"{engine} 权重",
                False,
                f"0/{len(statuses)} 档已预置（应位于 {missing[0].location}）",
                missing[0].hint,
            )
        for st in missing:
            if st.ok:
                continue
            # 逐档补一行「缺哪个」，指引只给一次（首个缺失项已带完整说明）
            print(f"      · 缺 {st.engine}/{st.model} — {st.detail}")


def _check_workspace(workspace: Optional[Path]) -> None:
    """📌 工作区 — 目录存在且可写(写探针实测)。"""
    print("\n📌 工作区")
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
    except Exception as exc:
        _check("可写", False, f"{ws} — {exc!r}", "检查目录权限或用 --workspace 换路径")


def _check_cookies() -> None:
    """📌 Cookies — 微信公众号 / 抖音的可选登录凭据。"""
    print("\n📌 Cookies（可选）")
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
        "浏览器导出 cookies.txt 放到项目根目录（见 docs_site 平台文档）",
    )


def _check_terms() -> None:
    """📌 ASR 术语库 — learn 累积的条数(读不到按空处理, 不算失败)。"""
    print("\n📌 ASR 术语库（可选）")
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


def run_doctor(workspace: Optional[Path] = None) -> int:
    """执行环境自检并打印报告。返回进程退出码。

    各自检项拆分为 ``_check_*`` 系列函数(输出顺序即调用顺序);
    退出码约定: ffmpeg 缺失返回 1, 其余情况一律 0 — doctor 的定位
    是"诊断报告", 非关键项缺失不应阻断脚本调用方。
    """
    print("🩺 MediaScribe 环境自检")
    print("=" * 50)

    _check_python()
    critical_ok = _check_av_tools()
    _check_device()
    _check_engines()
    _check_model_cache()
    _check_model_weights()
    _check_workspace(workspace)
    _check_cookies()
    _check_terms()

    print("\n" + "=" * 50)
    if critical_ok:
        print("✅ 自检完成 — 关键依赖就绪")
        return 0
    print("❌ 自检发现关键缺失（ffmpeg）— 请先修复再运行转录")
    return 1
