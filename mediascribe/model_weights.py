"""
模型权重清单 —— 离线产品必须预置的权重。

为什么要有这个模块
------------------
MediaScribe 是**完全离线**产品：所有推理权重都必须**预先放置**在机器上。
但「模型名 → 权重文件在哪」这件事此前是隐式的，缺了只能在跑到一半时炸：

* 2026-10-23 实测：``faster-whisper medium`` 在本机直接抛
  ``LocalEntryNotFoundError: Cannot find an appropriate cached snapshot
  folder`` —— 因为本机只缓存了 ``small`` 与 ``large-v3``，而 HuggingFace
  不可达 ⇒ 该档位**完全无法运行**（不是引擎缺陷，是权重没预置）。
* 同理 ``openai-whisper`` 的权重在 ``~/.cache/whisper/*.pt``，与
  faster-whisper 的 HuggingFace 缓存**是两套完全不同的目录约定**。

本模块把「某个 (engine, model) 需要什么权重、在哪、怎么预置」变成**可查询
的数据**，供三处使用：

1. :mod:`mediascribe.doctor` —— 诊断时直接报「缺哪个、怎么补」
2. 转录器加载失败时 —— 把 HuggingFace 的原始异常换成带指引的中文错误
3. 分发打包时 —— 可据此生成权重清单

目录约定
--------
======================  ==========================================
引擎                     权重位置
======================  ==========================================
faster-whisper           ``$HF_HUB_CACHE/models--Systran--faster-whisper-<model>/``
whisper (openai)         ``~/.cache/whisper/<model>.pt``
whisperx                 同 faster-whisper（其后端）+ 对齐模型
whisper-cpp              ``<models_dir>/ggml-<model>.bin``
======================  ==========================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: 引擎的规范名（与 CLI ``--engine`` 取值一致）
ENGINE_WHISPER = "whisper"
ENGINE_FASTER = "faster-whisper"
ENGINE_WHISPERX = "whisperx"
ENGINE_WHISPER_CPP = "whisper-cpp"


@dataclass(frozen=True)
class WeightSpec:
    """某个 (engine, model) 组合的权重规格。"""

    engine: str
    model: str
    #: 权重存放方式：``hf-cache`` / ``openai-ckpt`` / ``ggml-file`` / ``builtin``
    kind: str
    #: 人类可读的位置描述
    location: str
    #: 缺权重时给用户的预置指引
    hint: str
    #: 该组合是否支持本地离线预置（False 表示必须联网获取，如门控模型）
    offline_capable: bool = True

    def describe(self) -> str:
        return f"{self.engine}/{self.model} → {self.location}"


# ---------------------------------------------------------------------------
# 目录约定
# ---------------------------------------------------------------------------


def hf_hub_cache_dir() -> Path:
    """HuggingFace 缓存根目录（与 ``huggingface_hub`` 的解析一致）。"""
    env = os.environ.get("HF_HUB_CACHE", "").strip()
    if env:
        return Path(env)
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        return Path(hf_home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def openai_whisper_cache_dir() -> Path:
    """openai-whisper 权重目录（``~/.cache/whisper``）。"""
    env = os.environ.get("WHISPER_CACHE_DIR", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".cache" / "whisper"


def whisper_cpp_models_dir() -> Path:
    """whisper.cpp 的 GGML 权重目录（可用 ``MEDIASCRIBE_WHISPER_CPP_MODELS`` 覆盖）。"""
    env = os.environ.get("MEDIASCRIBE_WHISPER_CPP_MODELS", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".cache" / "mediascribe" / "whisper-cpp"


# ---------------------------------------------------------------------------
# 清单
# ---------------------------------------------------------------------------


def _hf_spec(engine: str, model: str) -> WeightSpec:
    return WeightSpec(
        engine=engine,
        model=model,
        kind="hf-cache",
        location=f"{hf_hub_cache_dir() / ('models--Systran--faster-whisper-' + model)}",
        hint=(
            "预置方式（任选其一）：\n"
            "  1) 有网时先跑一次：python -m mediascribe transcribe --engine faster-whisper "
            f"--model {model} --url <任意音频>（会下载到 HuggingFace 缓存）\n"
            "  2) 离线机：把 Systran/faster-whisper-{model} 的快照目录整个拷到"
            f" {hf_hub_cache_dir()}"
        ),
    )


def _openai_spec(model: str) -> WeightSpec:
    return WeightSpec(
        engine=ENGINE_WHISPER,
        model=model,
        kind="openai-ckpt",
        location=f"{openai_whisper_cache_dir() / (model + '.pt')}",
        hint=(
            "预置方式：把 openai/whisper 的 "
            f"{model}.pt 放到 {openai_whisper_cache_dir()}/\n"
            "  （可用 WHISPER_CACHE_DIR 环境变量改目录）"
        ),
    )


def _cpp_spec(model: str) -> WeightSpec:
    return WeightSpec(
        engine=ENGINE_WHISPER_CPP,
        model=model,
        kind="ggml-file",
        location=f"{whisper_cpp_models_dir() / ('ggml-' + model + '.bin')}",
        hint=(
            "预置方式：把 GGML 权重 "
            f"ggml-{model}.bin 放到 {whisper_cpp_models_dir()}/\n"
            "  （可用 MEDIASCRIBE_WHISPER_CPP_MODELS 环境变量改目录）"
        ),
    )


def weight_spec(engine: str, model: str) -> Optional[WeightSpec]:
    """返回 (engine, model) 的权重规格；未知组合返回 ``None``。"""
    engine = (engine or "").strip()
    model = (model or "").strip()
    if engine in (ENGINE_FASTER, ENGINE_WHISPERX):
        return _hf_spec(engine, model)
    if engine == ENGINE_WHISPER:
        return _openai_spec(model)
    if engine == ENGINE_WHISPER_CPP:
        return _cpp_spec(model)
    return None


def known_weight_pairs() -> List[Tuple[str, str]]:
    """列出本模块认识的 (engine, model) 组合。"""
    pairs: List[Tuple[str, str]] = []
    for engine in (ENGINE_FASTER, ENGINE_WHISPERX):
        for model in ("tiny", "base", "small", "medium", "large-v3"):
            pairs.append((engine, model))
    for model in ("tiny", "base", "small", "medium", "large-v3"):
        pairs.append((ENGINE_WHISPER, model))
        pairs.append((ENGINE_WHISPER_CPP, model))
    return pairs


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------


@dataclass
class WeightStatus:
    """权重就绪状态。"""

    engine: str
    model: str
    ok: bool
    location: str = ""
    hint: str = ""
    detail: str = ""

    def message(self) -> str:
        if self.ok:
            return f"权重就绪：{self.location}"
        return f"缺少权重 {self.engine}/{self.model}（应位于 {self.location}）\n{self.hint}"


def _hf_present(spec: WeightSpec) -> Tuple[bool, str]:
    root = Path(spec.location)
    if not root.exists():
        return False, "缓存目录不存在"
    # HuggingFace 快照目录里真正决定「可离线」的是 snapshots/<rev>/ 下有实体文件
    snapshots = root / "snapshots"
    if snapshots.exists():
        for rev in snapshots.iterdir():
            if rev.is_dir() and any(rev.iterdir()):
                return True, f"已缓存（{rev.name}）"
    blobs = root / "blobs"
    if blobs.exists() and any(blobs.iterdir()):
        return True, "已缓存（blob 存在）"
    return False, "快照目录为空"


def _file_present(spec: WeightSpec) -> Tuple[bool, str]:
    p = Path(spec.location)
    if p.exists() and p.stat().st_size > 0:
        return True, f"{p.stat().st_size / 1024 / 1024:.0f} MiB"
    return False, "文件不存在"


def check_weights(engine: str, model: str) -> WeightStatus:
    """检查 (engine, model) 的权重是否已预置。"""
    spec = weight_spec(engine, model)
    if spec is None:
        return WeightStatus(
            engine=engine,
            model=model,
            ok=False,
            location="(未知引擎)",
            hint="未知引擎，无法给出权重预置指引",
            detail="unknown-engine",
        )
    if spec.kind == "hf-cache":
        ok, detail = _hf_present(spec)
    else:
        ok, detail = _file_present(spec)
    return WeightStatus(
        engine=engine,
        model=model,
        ok=ok,
        location=spec.location,
        hint=spec.hint,
        detail=detail,
    )


def preflight_all() -> Dict[Tuple[str, str], WeightStatus]:
    """扫描所有已知组合的权重就绪状态（供 doctor / 打包清单用）。"""
    return {pair: check_weights(*pair) for pair in known_weight_pairs()}


def missing_hint(engine: str, model: str) -> str:
    """缺权重时给用户的一句话指引（供异常消息拼接）。"""
    spec = weight_spec(engine, model)
    if spec is None:
        return f"未知引擎 {engine!r}，无法给出权重预置指引。"
    return spec.hint
