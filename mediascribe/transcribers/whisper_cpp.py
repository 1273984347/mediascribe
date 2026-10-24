"""
whisper.cpp 转录器（C++/GGML 引擎）—— 资源受限场景的第三条腿。

定位
----
2026-10-23 的本机实测（RTX 4060 Laptop / i7-13650HX / 16 GB）：

=================  ============  ==============  ==============
引擎                档位           RTF            峰值内存
=================  ============  ==============  ==============
faster-whisper     small (CUDA)   0.1336          1167 MiB
faster-whisper     large-v3(CPU)  1.334           3613 MiB
**whisper.cpp**     **tiny (CPU)**  **0.0504**     **291 MiB**
=================  ============  ==============  ==============

⇒ whisper.cpp 是三者里**最省内存、最快（CPU 档）**的方案，也是唯一能在
「无 GPU + 4 GB 内存老机器」上跑起来的路线。但它需要**外部二进制**与
**GGML 权重**预置（本产品要求完全离线 ⇒ 权重必须预置，不能到时再下）。

与其它引擎的差异（已按 2026-10-23 实测处理）
------------------------------------------
* **中文默认输出繁体** ⇒ 已通过简体 ``initial_prompt`` 引导 + 输出层
  归一化处理（见 :mod:`mediascribe.zh_normalize`）。
* **默认不做 VAD** ⇒ 噪声音频上幻觉严重（实测重度噪声档错误率 84–86%）。
  本实现**默认启用 VAD**（若 VAD 模型已预置），否则退回无 VAD 并告警。
* 需要 16 kHz mono WAV ⇒ 其它格式用 ffmpeg 临时转码。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from ..zh_normalize import simplified_initial_prompt
from .base import Transcriber

logger = logging.getLogger(__name__)

#: whisper.cpp 的 GGML 权重目录（可用 MEDIASCRIBE_WHISPER_CPP_MODELS 覆盖）
DEFAULT_MODELS_DIR = Path.home() / ".cache" / "mediascribe" / "whisper-cpp"

#: 常见���预编译二进制安装位置（按序探测）
_BIN_CANDIDATE_NAMES = ("whisper-cli.exe", "whisper-cli")
#: 旧版 whisper.cpp 的 CLI 叫 ``main`` —— 名字太模糊，**只在 whisper.cpp
#: 专属目录里找**，绝不走 PATH（否则会命中 Windows 的 main.CPL 之类）。
_BIN_LEGACY_NAMES = ("main.exe", "main")
_BIN_SEARCH_DIRS = (
    Path.home() / ".cache" / "mediascribe" / "whisper-cpp" / "bin" / "Release",
    Path.home() / ".cache" / "mediascribe" / "whisper-cpp" / "bin",
    Path.home() / ".cache" / "mediascribe" / "whisper-cpp",
    Path("C:/Program Files/whisper.cpp/bin"),
)

#: 二进制最小合理体积（whisper.cpp 的 CLI 约 0.5–2 MB；Windows 系统里的
#: main.CPL 只有几十 KB ⇒ 用体积把系统文件挡在外面）。
_MIN_BIN_BYTES = 100 * 1024

#: Windows 系统目录（里面的同名文件不是 whisper.cpp）
_SYSTEM_DIRS = ("C:/Windows/System32", "C:/Windows/SysWOW64")


def _is_plausible_binary(path: Path) -> bool:
    """排除「名字像、实际不是」的系统文件。

    实测踩坑：``shutil.which("main.exe")`` 在 Windows 上会返回
    ``C:\\Windows\\system32\\main.CPL``（控制面板）⇒ 曾把系统文件当成
    whisper.cpp 的二进制，导致引擎被误判为「可用」。
    """
    try:
        if not path.is_file() or path.stat().st_size < _MIN_BIN_BYTES:
            return False
    except OSError:
        return False
    normalized = str(path).replace("\\", "/").lower()
    return not any(normalized.startswith(d.lower()) for d in _SYSTEM_DIRS)


#: VAD 模型文件名（silero v6.2，864 KB）
_VAD_MODEL_NAMES = ("ggml-silero-v6.2.0.bin", "ggml-silero-v5.1.2.bin")


def models_dir() -> Path:
    """GGML 权重目录。"""
    env = os.environ.get("MEDIASCRIBE_WHISPER_CPP_MODELS", "").strip()
    return Path(env) if env else DEFAULT_MODELS_DIR


def find_binary() -> Optional[Path]:
    """定位 whisper.cpp 的可执行文件。

    查找顺序：``MEDIASCRIBE_WHISPER_CPP_BIN`` 环境变量 → PATH → 常见安装目录。
    找不到返回 ``None``（不抛异常 —— 让上层按「引擎不可用」处理）。
    """
    env = os.environ.get("MEDIASCRIBE_WHISPER_CPP_BIN", "").strip()
    if env:
        p = Path(env)
        if p.exists() and _is_plausible_binary(p):
            return p
    for name in _BIN_CANDIDATE_NAMES:
        found = shutil.which(name)
        if found and _is_plausible_binary(Path(found)):
            return Path(found)
    for d in _BIN_SEARCH_DIRS:
        for name in (*_BIN_CANDIDATE_NAMES, *_BIN_LEGACY_NAMES):
            p = d / name
            if p.exists() and _is_plausible_binary(p):
                return p
    return None


def missing_runtime_dlls(binary: Path) -> List[str]:
    """列出 whisper.cpp 运行时缺失的 DLL（官方包是动态链接的）。

    实测踩坑：只预置 ``whisper-cli.exe`` 而漏掉同目录的 ``whisper.dll`` /
    ``ggml*.dll`` ⇒ 进程以 ``STATUS_DLL_NOT_FOUND``（0xC0000135）静默退出，
    没有任何可读错误信息。
    """
    parent = binary.parent
    if not parent.exists():
        return []
    dlls = {p.name.lower() for p in parent.glob("*.dll")}
    if not dlls:
        return []  # 静态链接构建 ⇒ 不需要运行时 DLL
    required = ("whisper.dll", "ggml.dll")
    return [name for name in required if name not in dlls]


def find_model(model: str) -> Optional[Path]:
    """定位 GGML 权重文件（``ggml-<model>.bin``）。"""
    d = models_dir()
    for name in (f"ggml-{model}.bin", f"ggml-{model}.q5_0.bin", f"ggml-{model}-q5_0.bin"):
        p = d / name
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def find_vad_model() -> Optional[Path]:
    """定位 VAD 模型（可选 —— 没有它就只能做无 VAD 转写）。"""
    d = models_dir()
    for name in _VAD_MODEL_NAMES:
        p = d / name
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def is_available() -> bool:
    """引擎是否可用（二进制存在即可 —— 不需要 Python 依赖）。"""
    return find_binary() is not None


def _to_wav_16k(src: Path, dst: Path) -> bool:
    """转成 16 kHz mono PCM16 WAV（whisper.cpp 的输入要求）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
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
            timeout=600,
        )
        return True
    except Exception as exc:
        logger.warning("whisper.cpp: 音频转码失败: %s", exc)
        return False


def _parse_offsets(value: Any) -> float:
    """把 whisper.cpp 的 ``offsets.from/to``（毫秒）转成秒。"""
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return 0.0


class WhisperCppTranscriber(Transcriber):
    """whisper.cpp 转录器。

    Parameters
    ----------
    model : str
        Whisper 档位名（``"tiny"`` / ``"base"`` / ``"small"`` / ...），
        对应 ``ggml-<model>.bin``。
    device : str, optional
        仅 ``"cpu"`` 有意义（本实现调预编译的 CPU 版二进制；CUDA 版需要
        另行预置带 CUDA 的二进制包，届时用同样的接口）。
    binary : Path, optional
        whisper.cpp 可执行文件路径。``None`` 时自动探测。
    vad : bool
        是否启用引擎层 VAD。默认 **False**。

        实测依据（2026-10-24，tiny 模型、中文语料、同一台机器）：

        ==========================  ==========  ==========
        音频                          VAD 关      VAD 开
        ==========================  ==========  ==========
        干净中文                       **7.38%**   19.67%
        重度环境噪声                   94.67%      **81.97%**
        ==========================  ==========  ==========

        ⇒ VAD **只在噪声场景**明显改善（12.7 个百分点），在**干净音频上反而
        恶化**（12.3 个百分点 —— tiny 模型的 VAD 阈值偏激进，会切掉有效语音）。
        而 MediaScribe 管线**本身已有** VAD 与分块逻辑（``webrtcvad`` +
        chunked transcriber）⇒ 引擎层再开一次属于重复处理。
        故默认关闭，按需开启。
    threads : int
        推理线程数。``None`` 时按 CPU 物理核的一半。
    """

    name = "whisper-cpp"

    def __init__(
        self,
        model: str = "tiny",
        device: Optional[str] = None,
        *,
        binary: Optional[Path] = None,
        vad: bool = False,
        threads: Optional[int] = None,
    ):
        self.model_name = model
        self.device = device or "cpu"
        self._binary = binary
        self.vad = vad
        self.threads = threads

    # -- 资源定位 ----------------------------------------------------------
    def _resolve_binary(self) -> Path:
        if self._binary is not None:
            return Path(self._binary)
        found = find_binary()
        if found is None:
            raise RuntimeError(
                "未找到 whisper.cpp 可执行文件。\n"
                "  预置方式：下载官方预编译包并解压，例如\n"
                "    https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip\n"
                "  然后设置环境变量 MEDIASCRIBE_WHISPER_CPP_BIN 指向其中的 whisper-cli.exe\n"
                "  （或解压到 ~/.cache/mediascribe/whisper-cpp/bin/Release/ 下自动探测）\n"
                "  ⚠️ 必须把 **整个目录**（含 whisper.dll / ggml*.dll）一起预置 —— "
                "官方包是动态链接的，漏 DLL 会以 STATUS_DLL_NOT_FOUND 静默失败"
            )
        missing = missing_runtime_dlls(found)
        if missing:
            raise RuntimeError(
                f"whisper.cpp 运行时 DLL 不完整：缺少 {', '.join(missing)} —— "
                f"官方预编译包是动态链接的，必须把 whisper-cli.exe 与同目录的 "
                f"whisper.dll / ggml*.dll 一起预置到 {found.parent}"
            )
        self._binary = found
        return found

    def _resolve_model(self) -> Path:
        found = find_model(self.model_name)
        if found is None:
            raise RuntimeError(
                f"未找到 whisper.cpp 的 GGML 权重 ggml-{self.model_name}.bin。\n"
                f"  应位于：{models_dir()}\n"
                "  预置方式：把 ggml 权重文件放到该目录"
                "（可用 MEDIASCRIBE_WHISPER_CPP_MODELS 环境变量改目录）"
            )
        return found

    def _resolve_threads(self) -> int:
        if self.threads:
            return self.threads
        try:
            import psutil  # type: ignore

            cores = psutil.cpu_count(logical=False) or (os.cpu_count() or 2)
        except Exception:
            cores = (os.cpu_count() or 2) // 2
        return max(1, cores // 2)

    # -- 转录 --------------------------------------------------------------
    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: Optional[str] = None,
        progress: Optional[Any] = None,
        **kwargs,
    ) -> dict[str, Any]:
        binary = self._resolve_binary()
        model_file = self._resolve_model()
        audio_path = Path(audio_path)

        language = kwargs.get("language")
        effective_prompt = simplified_initial_prompt(prompt, language)

        tmp_dir = Path(tempfile.mkdtemp(prefix="mediascribe-cpp-"))
        wav_path = tmp_dir / "input.wav"
        out_prefix = tmp_dir / "out"
        try:
            if audio_path.suffix.lower() == ".wav":
                shutil.copy2(audio_path, wav_path)
            elif not _to_wav_16k(audio_path, wav_path):
                raise RuntimeError(f"whisper.cpp: 音频转码失败（{audio_path}）")

            cmd: List[str] = [
                str(binary),
                "-m",
                str(model_file),
                "-f",
                str(wav_path),
                "-t",
                str(self._resolve_threads()),
                "-oj",
                "-of",
                str(out_prefix),
                "-nt",
            ]
            if language:
                cmd += ["-l", language]
            if effective_prompt:
                cmd += ["--prompt", effective_prompt]
            vad_model = find_vad_model() if self.vad else None
            if self.vad and vad_model is None:
                logger.warning(
                    "whisper.cpp: 未找到 VAD 模型（%s），退回无 VAD 转写 —— "
                    "噪声音频上可能出现明显幻觉",
                    models_dir() / _VAD_MODEL_NAMES[0],
                )
            if vad_model is not None:
                cmd += ["--vad", "--vad-model", str(vad_model)]

            if progress:
                logger.info("whisper.cpp 转录中: %s", audio_path.name)

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=False)
            json_path = Path(str(out_prefix) + ".json")
            if not json_path.exists():
                tail = (proc.stderr or proc.stdout or "")[-800:]
                # 0xC0000135 = STATUS_DLL_NOT_FOUND —— whisper.cpp 官方包是
                # **动态链接**的，whisper-cli.exe 依赖同目录的 whisper.dll /
                # ggml*.dll；只拷一个 exe 就会以这个退出码失败（实测踩到）。
                if proc.returncode in (0xC0000135, -1073741515):
                    raise RuntimeError(
                        "whisper.cpp 缺少运行时 DLL（STATUS_DLL_NOT_FOUND）—— "
                        "官方预编译包是动态链接的，必须把 whisper-cli.exe 与同目录的 "
                        f"whisper.dll / ggml*.dll 一起预置到 {binary.parent}"
                    )
                raise RuntimeError(f"whisper.cpp 转写失败（exit={proc.returncode}）：{tail}")
            data = json.loads(json_path.read_text(encoding="utf-8"))
            items = data.get("transcription") or []
            segments = [
                {
                    "text": (item.get("text") or "").strip(),
                    "start": _parse_offsets((item.get("offsets") or {}).get("from")),
                    "end": _parse_offsets((item.get("offsets") or {}).get("to")),
                }
                for item in items
            ]
            text = " ".join(str(s.get("text") or "") for s in segments).strip()
            return {
                "text": text,
                "segments": segments,
                "language": language,
                "model": self.model_name,
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
