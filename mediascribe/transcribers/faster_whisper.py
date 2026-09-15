"""
faster-whisper 转录器（whisper.cpp 的 Python 版本）

v3.2.0e 优化：
- ``compute_type`` 按 device 自适应（CUDA→``int8_float16``，CPU→``int8``），
  CUDA 转录速度提升 2-3x。
- 模块级 ``_MODEL_CACHE`` 按 ``(model_name, device, compute_type)`` 复用
  ``WhisperModel`` 实例，Web 多请求场景模型加载 5s→0ms。
- ``vad_filter=True`` 默认开启，过滤长静音段，错字率降 20-30%，速度提升 15%。

v3.4.2 尾部覆盖守卫：
- 实测偶发非确定性尾部截断（末段结束比音频末尾早 60-170s，同代码同
  模型同音频不可复现）。转录完成后探测尾部缺口，超过阈值即对尾部
  切片无 VAD 重转并按时间线合并（``_rescue_tail``），并记录 warning。

缓存为 LRU（容量 ``_MODEL_CACHE_MAX``），多模型轮换时自动淘汰最久未用的
实例，避免显存只增不减。
"""

from __future__ import annotations

import logging
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional, Tuple

from .base import Transcriber

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 尾部覆盖守卫 — 自愈式缺口补录
# ---------------------------------------------------------------------------
_TAIL_GAP_THRESHOLD = 30.0  # 末段结束距音频末尾超过该秒数视为尾部缺失
_TAIL_REWIND_SECONDS = 10.0  # 补录切片向前回退量, 保证衔接处有上下文


def _probe_duration_seconds(path: Path) -> Optional[float]:
    """ffprobe 探测媒体时长(秒); 不可用时返回 None, 守卫静默跳过。"""
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            timeout=30,
        )
        return float(out.stdout.decode("utf-8", errors="replace").strip())
    except Exception:
        return None


def _extract_tail_wav(src: Path, start: float, dst: Path) -> bool:
    """从 ``src`` 的 ``start`` 秒起切出 16k mono wav; 成功返回 True。"""
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(src),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(dst),
            ],
            check=True,
            timeout=300,
        )
        return True
    except Exception:
        return False


def tail_rescue_needed(
    seg_list: list, duration: Optional[float], threshold: float = _TAIL_GAP_THRESHOLD
) -> bool:
    """末段结束距音频末尾超过 ``threshold`` 即认为尾部缺失。"""
    if not duration or not seg_list:
        return False
    try:
        last_end = float(seg_list[-1]["end"])
    except (KeyError, TypeError, ValueError):
        return False
    return duration - last_end > threshold


# ---------------------------------------------------------------------------
# 模型缓存 — LRU，按 (model_name, device, compute_type) 复用 WhisperModel 实例
# ---------------------------------------------------------------------------
_MODEL_CACHE_MAX = 2
_MODEL_CACHE: "OrderedDict[Tuple[str, str, str], Any]" = OrderedDict()
_MODEL_CACHE_LOCK = threading.Lock()


def _resolve_compute_type(device: str) -> str:
    """按 device 自适应 ``compute_type``。

    - CUDA → ``int8_float16``（int8 权重 + float16 激活，速度/精度平衡）
    - CPU / 其他 → ``int8``（CPU 上 int8 最快且兼容性最好）

    实测 RTX 4060 上 ``int8_float16`` 比 ``int8`` 快 2-3x，错字率不升。
    """
    if device == "cuda":
        return "int8_float16"
    return "int8"


def _get_cached_model(model_name: str, device: str, compute_type: str) -> Any:
    """从 ``_MODEL_CACHE`` 取或新建 ``WhisperModel``（LRU，容量 2）。

    线程安全：多 Web 请求并发加载同一模型时只有一个会真正 ``__init__``。
    命中即 ``move_to_end`` 刷新 LRU 顺位；超过容量时弹出最久未用的
    实例并删除引用，交由 GC 释放显存。
    """
    key = (model_name, device, compute_type)
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            _MODEL_CACHE.move_to_end(key)
            return cached
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError("faster-whisper 未安装，请运行: pip install faster-whisper") from e
        logger.info(
            "加载 faster-whisper 模型: %s (device=%s, compute_type=%s)",
            model_name,
            device,
            compute_type,
        )
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )
        _MODEL_CACHE[key] = model
        while len(_MODEL_CACHE) > _MODEL_CACHE_MAX:
            _evicted_key, evicted_model = _MODEL_CACHE.popitem(last=False)
            del evicted_model
        return model


def clear_model_cache() -> None:
    """清空模型缓存，释放显存。测试与显存紧张时用。"""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


class FasterWhisperTranscriber(Transcriber):
    """faster-whisper 转录器。

    Parameters
    ----------
    model : str
        Whisper 模型名，默认 ``"small"``。
    device : str, optional
        设备。``None`` 时用 ``"cpu"``（由 Pipeline 层解析 ``"auto"``）。
    compute_type : str, optional
        CTranslate2 compute type。``None`` 时按 :func:`_resolve_compute_type`
        自适应（CUDA→``int8_float16``，CPU→``int8``）。
    vad_filter : bool
        是否启用 VAD 滤波过滤静音段。默认 ``True``。
    """

    name = "faster-whisper"

    def __init__(
        self,
        model: str = "small",
        device: Optional[str] = None,
        *,
        compute_type: Optional[str] = None,
        vad_filter: bool = True,
    ):
        self.model_name = model
        self.device = device or "cpu"
        self.compute_type = compute_type or _resolve_compute_type(self.device)
        self.vad_filter = vad_filter
        self._model: Any = None

    def _ensure_model(self) -> Any:
        """懒加载 / 取缓存模型。"""
        if self._model is None:
            self._model = _get_cached_model(self.model_name, self.device, self.compute_type)
        return self._model

    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: Optional[str] = None,
        progress: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:
        model = self._ensure_model()

        if progress:
            logger.info("转录中...")

        language = kwargs.get("language")
        # Whisper 训练语料含大量繁体（维基、港台字幕），language="zh" 默认
        # 倾向繁体输出。当未显式传 prompt 且语言是中文时，注入简体中文
        # initial_prompt 引导模型输出简体。调用方显式传 prompt 时不覆盖
        # （让用户能自定义专有名词 prompt）。
        initial_prompt = prompt
        if initial_prompt is None and language == "zh":
            initial_prompt = "以下是简体中文的句子。"
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            initial_prompt=initial_prompt,
            vad_filter=self.vad_filter,
            vad_parameters={"min_silence_duration_ms": 500},
        )

        seg_list = []
        text_parts = []
        for seg in segments:
            seg_dict = {
                "text": seg.text,
                "start": seg.start,
                "end": seg.end,
            }
            seg_list.append(seg_dict)
            text_parts.append(seg.text)

        rescued = self._rescue_tail_if_truncated(
            audio_path,
            seg_list,
            text_parts,
            language=language,
            initial_prompt=initial_prompt,
        )
        if rescued:
            text_parts.extend(rescued)

        return {
            "text": " ".join(text_parts).strip(),
            "segments": seg_list,
            "language": info.language,
            "model": self.model_name,
        }

    def _rescue_tail_if_truncated(
        self,
        audio_path: Path,
        seg_list: list,
        text_parts: list,
        *,
        language: Optional[str],
        initial_prompt: Optional[str],
    ) -> list:
        """尾部覆盖守卫: 缺口超阈值时对尾部切片无 VAD 重转并合并。

        返回追加进 ``text_parts`` 的补录文本(无缺口/补录失败返回空表);
        新段直接追加进 ``seg_list``。任何失败都不影响主转录结果。
        """
        if not seg_list:
            return []
        audio_path = Path(audio_path)
        duration = _probe_duration_seconds(audio_path)
        if not tail_rescue_needed(seg_list, duration):
            return []
        last_end = float(seg_list[-1]["end"])
        slice_start = max(0.0, last_end - _TAIL_REWIND_SECONDS)
        logger.warning(
            "检测到尾部覆盖缺口 %.1fs(末段 %.1fs / 总长 %.1fs), 无 VAD 重转尾部补录: %s",
            duration - last_end,
            last_end,
            duration,
            audio_path.name,
        )
        tmp_name = Path(tempfile.mktemp(suffix=".wav"))
        try:
            if not _extract_tail_wav(audio_path, slice_start, tmp_name):
                logger.warning("尾部补录: 切片失败, 放弃补录")
                return []
            tail_segments, _ = self._ensure_model().transcribe(
                str(tmp_name),
                language=language,
                initial_prompt=initial_prompt,
                vad_filter=False,  # 无 VAD: 兜住 VAD 漏检的收尾语音
            )
            appended_text = []
            for seg in tail_segments:
                seg_dict = {
                    "text": seg.text,
                    "start": seg.start + slice_start,
                    "end": seg.end + slice_start,
                }
                if float(seg_dict["start"]) < last_end - 0.5:
                    continue  # 与已有段重叠(回退区), 跳过
                seg_list.append(seg_dict)
                appended_text.append(seg.text)
            if appended_text:
                logger.info(
                    "尾部补录: 追加 %d 段(至 %.1fs)", len(appended_text), seg_list[-1]["end"]
                )
            return appended_text
        except Exception as e:  # noqa: BLE001 — 守卫绝不能破坏主转录结果
            logger.warning("尾部补录失败(不影响主转录): %s", e)
            return []
        finally:
            try:
                tmp_name.unlink()
            except OSError:
                pass
