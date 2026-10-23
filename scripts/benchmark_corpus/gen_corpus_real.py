"""
v3.2.0b benchmark corpus 生成器 —— **真实语音**版（离线合成）。

背景
----
``scripts/benchmark_corpus/gen_corpus.py`` 只生成 5 秒正弦波占位，注释里写明
「真实长音频由 maintainer 本地用 ffmpeg / 麦克风 / 公共数据集生成，然后发布到
Hugging Face Datasets」。本机环境 **HuggingFace 不可达**，走不了这条路。

但 Windows 自带 SAPI 语音（3 个普通话音色 + 1 个英语音色），因此可以**完全离线**
合成出真实语音语料，且参考文本天然已知 ⇒ 可以精确计算 CER / WER。

本脚本产出的语料满足：

* 16 kHz / mono / 16-bit PCM（与 ASR 引擎输入规格一致）
* 每个文件配一份 reference JSON，schema 沿用 ``mediascribe-benchmark-corpus/v1``
* 一条「轻度环境噪声」变体，用于区分引擎在干净音频上的真实差距

调用
----
::

    python scripts/benchmark_corpus/gen_corpus_real.py \
        --out output/bench/corpus

产物不��库（``output/*`` 已被 .gitignore 忽略）；入库的只有本脚本与
``sapi_tts.ps1``（合成方法本身可复现）。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

CORPUS_SCHEMA = "mediascribe-benchmark-corpus/v1"

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2

#: 噪声随机种子 —— **固定值是基准可复现的前提**。
#: ffmpeg 的 ``anoisesrc`` 默认每次都产生不同噪声，同参数重建的音频难度
#: 会明显漂移（实测同参数下错误率可从 23.8% 跳到 45.9%），基准就失去可比性。
NOISE_SEED = 20261023

REPO_ROOT = Path(__file__).resolve().parents[2]
SAPI_TTS = Path(__file__).resolve().parent / "sapi_tts.ps1"


# ---------------------------------------------------------------------------
# 语料定义
# ---------------------------------------------------------------------------


@dataclass
class Clip:
    """一条语料的定义。"""

    name: str
    language: str  # zh | en | mixed
    voice: str  # SAPI voice name
    text: str
    topic: str
    speakers: int = 1
    #: 噪声变体基名；非空表示额外产出一个带噪版本
    noisy_of: Optional[str] = None
    #: 噪声强度（amix weights 里的噪声权重）
    noise_level: float = 0.35
    #: SAPI 语速（-10 最慢 .. 10 最快）。加速更接近真实口语，也更难识别。
    rate: int = 0
    #: 是否产出「重度退化」变体：强噪声 + 低通（手机录音频响）+ 混响
    harsh: bool = False
    harsh_noise: float = 0.7
    harsh_lowpass_hz: int = 2200


#: 通用中文播报稿 —— 干净、无术语，用于跨引擎基线对比。
TEXT_NEWS = """各位观众大家好，欢迎收看本期的科技观察节目。
今天我们讨论的话题是，人工智能模型正在怎样改变软件开发的工作方式。
过去五年间，大语言模型从实验室走进了日常的工程流程。
越来越多的团队开始用模型辅助编写代码，编写测试，甚至参与系统设计。
但与此同时，一个现实的问题也随之出现，速度提升的同时，代码的可维护性是否也在同步提升。
有研究者指出，模型生成的代码越容易被接受，评审环节就越容易被跳过。
而一旦跳过评审，隐藏的技术债就会在后期集中爆发。
我们认为，工具可以改变效率，但很难替人承担判断的责任。
这就是本期节目的全部内容，感谢您的观看，我们下期再见。"""


#: 术语密集稿件 —— 贴近 MediaScribe 的真实痛点：中文专名、公式名、
#: 数字与单位混排。这是区分「谁更懂中文」的关键样本。
TEXT_TERMS = """欢迎收听今天的课程，我们继续讨论统计物理中的相变问题。
先回顾一下伊辛模型，它是描述自旋相互作用的最经典模型之一。
在一维情况下，伊辛模型在绝对零度只存在两种构型，但在二维情况下，伊辛模型可以发生连续相变。
这个相变属于普适类，由临界指数描述，其中比热容指数大约是七比四。
需要强调的是，这里的相变与朗道理论给出的平均场结果并不一致。
朗道理论预言的临界指数是三分之一，与实验测量的七比四存在明显偏差。
这种偏差说明涨落在高阶近似中不可忽略。
另外我们还要提到蒙特卡洛方法在统计力学中的地位。
蒙特卡洛方法通过重要性采样估计配分函数，对玻璃态这类难问题尤其有效。
最后提醒大家，课程作业将在下周一截止，请及时提交。"""


#: 口语短视频风格 —— 有语气词、口语缩写、数字混排，模拟真实短视频转写。
TEXT_COLLOQUIAL = """哎你们看啊，这个新款相机刚才那个博主测评说啊，续航特别猛。
他用的那块电池是一万七千五百毫安时的，比上一代多了大概百分之二十三。
然后拍视频的时候，他说在百分之四亮度下能连续录四个半小时。
我觉得这个数据有点吹牛了，因为上次我买的那块二手电池啊，掉电特别快。
不过呢，这个价格三千二百九十九块我觉得还是可以的。
你们要是想看我详细测评的话，记得给我点个关注。"""


#: 中英混说 —— 中文语料里混英文术语与整句，是真实内容创作者常见形态。
TEXT_MIXED = """今天我们聊一个 topic，就是 large language model 在 code review 里面的实际作用。
很多人以为它只是 autocomplete，其实远远不止。
举一个 example，我们团队用它做 first pass review，能提前抓出大概百分之四十的低级错误。
当然它也有 blind spot，比如它不会意识到这个改动和三个月前的另一个 commit 有冲突。
所以我们的 practice 是，AI 先做 first pass，human 做 final decision。
这句话我 always tell my students，never let the model be the last reviewer.
谢谢大家，我们下期 see you。"""


#: 英语对照 —— TED 风格独白。
TEXT_EN = """Let me begin with a question that sounds simple but is surprisingly difficult to answer.
Why do some technologies succeed while others quietly fade into obscurity?
The answer is rarely technical. It has much more to do with how a technology fits into the daily habits of ordinary people.
Consider the telephone. It took nearly fifty years before the telephone became something ordinary families actually wanted in the living room.
We owned them, but we did not use them. The line was busy, the operators were rude, and the pricing felt like a punishment.
Now imagine that same device, with an interface that takes no effort to understand, running on a network that costs almost nothing.
Suddenly the technology is not a privilege of the wealthy. It is simply part of the fabric of daily life.
And this is the pattern that repeats. Every technology that eventually becomes ordinary first has to make a promise that is easy to understand.
The promise matters more than the sophistication underneath."""


CLIPS: List[Clip] = [
    Clip(
        name="01_zh_huihui.wav",
        language="zh",
        voice="Microsoft Huihui Desktop",
        text=TEXT_NEWS,
        topic="科技播报稿（普通话标准女声）",
        noisy_of="01_zh_huihui.wav",
    ),
    Clip(
        name="02_zh_kangkang.wav",
        language="zh",
        voice="Microsoft Kangkang",
        text=TEXT_NEWS,
        topic="科技播报稿（普通话另一音色）",
    ),
    Clip(
        name="03_zh_yaoyao.wav",
        language="zh",
        voice="Microsoft Yaoyao",
        text=TEXT_NEWS,
        topic="科技播报稿（普通话第三音色）",
    ),
    Clip(
        name="04_zh_terms.wav",
        language="zh",
        voice="Microsoft Huihui Desktop",
        text=TEXT_TERMS,
        topic="术语密集课程稿（伊辛模型/朗道理论/蒙特卡洛）",
    ),
    Clip(
        name="05_zh_colloquial.wav",
        language="zh",
        voice="Microsoft Huihui Desktop",
        text=TEXT_COLLOQUIAL,
        topic="口语短视频测评（语气词/数字混排）",
    ),
    Clip(
        name="06_en_zira.wav",
        language="en",
        voice="Microsoft Zira Desktop",
        text=TEXT_EN,
        topic="TED 风格独白",
    ),
    Clip(
        name="07_mixed_huihui.wav",
        language="mixed",
        voice="Microsoft Huihui Desktop",
        text=TEXT_MIXED,
        topic="中英混说（术语+整句英文）",
    ),
    # --- 重度退化档 ------------------------------------------------------
    # 纯 TTS 音频对 ASR 太容易（干净音频错误率 0%，无法区分引擎优劣）。
    # 这两条叠加「加速语速 + 强噪声 + 手机录音频响 + 混响」，
    # 把错误率推到有区分度的区间，用来比较引擎的中文鲁棒性。
    Clip(
        name="01h_zh_huihui_harsh.wav",
        language="zh",
        voice="Microsoft Huihui Desktop",
        text=TEXT_NEWS,
        topic="科技播报稿（快速语速 + 重度环境噪声 + 手机录音频响）",
        rate=2,
        harsh=True,
        harsh_noise=0.7,
    ),
    Clip(
        name="04h_zh_terms_harsh.wav",
        language="zh",
        voice="Microsoft Huihui Desktop",
        text=TEXT_TERMS,
        topic="术语密集课程稿（加速语速 + 中度噪声）",
        # 术语稿本身就有大量专名（伊辛模型/朗道理论/蒙特卡洛），
        # 退化强度必须低于通用稿 —— 实测 0.7 噪声 + 2200Hz 低通会把它推到
        # 61% 错误率（所有引擎都饱和，失去区分度），故降到 0.45 / 4000Hz。
        rate=1,
        harsh=True,
        harsh_noise=0.45,
        harsh_lowpass_hz=4000,
    ),
]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    """跑一条命令，失败时把 stdout/stderr 一起抛出（便于定位）。"""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"命令失败 (exit={proc.returncode}): {' '.join(cmd)}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


def which(name: str) -> str:
    """定位可执行文件；缺失时给出可操作的报错。"""
    path = shutil.which(name)
    if not path:
        raise RuntimeError(
            f"找不到可执行文件 '{name}'。本脚本需要："
            "pwsh（Windows 10+ 自带，用于调 SAPI）、ffmpeg + ffprobe。"
        )
    return path


def normalize_for_cer(text: str, language: str) -> str:
    """把参考文本归一化成 CER/WER 的比较口径。

    * 中文（zh / mixed）：**按字**比较 —— 去掉所有标点、空白、英文标点。
      理由：中文 ASR 的错误几乎都落在字上，用词级 WER 会低估错误率；
      仓库既有 ``benchmark_transcribers.char_wer`` 也是字符级口径。
    * 英文（en）：**按词**比较 —— 小写、剥标点、空白分词。

    ``mixed`` 的英文部分按词、中文部分按字，统一放进同一个字符串后
    再分词会混淆口径，因此 mixed 走「中文按字 + 英文按词」混合切分：
    连续的拉丁字母串按词计，其余非空白字符按字计。
    """
    text = text.strip()
    if language == "en":
        words = re.findall(r"[a-z0-9']+", text.lower())
        return " ".join(words)
    if language == "mixed":
        tokens: List[str] = []
        for chunk in re.findall(r"[A-Za-z0-9']+|[^\s]", text):
            if chunk.isascii() and chunk.isalnum():
                tokens.append(chunk.lower())
            elif not re.match(r"[A-Za-z0-9'\s]", chunk):
                tokens.append(chunk)  # 中文字/标点：标点在下面剔除
        # 剔除纯标点
        tokens = [t for t in tokens if re.search(r"[0-9A-Za-z\u4e00-\u9fff]", t)]
        return " ".join(tokens)
    # zh
    chars = [c for c in text if re.search(r"[0-9A-Za-z\u4e00-\u9fff]", c)]
    return " ".join(chars)


def split_sentences(text: str) -> List[str]:
    """按中英文句末标点切句，保留标点。"""
    parts = re.split(r"(?<=[。！？；.!?;])\s*", text)
    return [p.strip() for p in parts if p.strip()]


def probe_duration(path: Path) -> float:
    out = _run(
        [
            which("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ]
    ).stdout.strip()
    return round(float(out.splitlines()[0]), 3)


def build_reference(clip: Clip, wav_path: Path, duration_s: float) -> Dict:
    """按仓库既有 schema 写 reference JSON（增量加归一化字段）。"""
    sentences = split_sentences(clip.text)
    # 按字符数比例把总时长摊到每句
    total_chars = sum(len(s) for s in sentences) or 1
    transcript = []
    cursor = 0.0
    for sent in sentences:
        share = len(sent) / total_chars
        end = min(duration_s, cursor + share * duration_s)
        transcript.append(
            {
                "start": round(cursor, 3),
                "end": round(end, 3),
                "speaker": "S1",
                "text": sent,
            }
        )
        cursor = end
    return {
        "schema_version": CORPUS_SCHEMA,
        "file": clip.name,
        "language": clip.language,
        "duration_s": duration_s,
        "voice": clip.voice,
        "topic": clip.topic,
        "speakers": clip.speakers,
        "wer_notes": (
            "Offline SAPI synthesis. Reference text is exact by construction "
            "(TTS reads the text verbatim), so CER/WER measures pure ASR error "
            "with no human annotation noise. Caveat: synthetic speech is cleaner "
            "and more uniform than real recordings, so absolute error rates are "
            "optimistic; use it for RELATIVE comparison between engines."
        ),
        "transcript": transcript,
        "full_text": clip.text,
        # 增量字段：归一化后的比较基准（zh 按字 / en 按词 / mixed 混合）
        "full_text_nopunct": normalize_for_cer(clip.text, clip.language),
    }


def synthesize(clip: Clip, work_dir: Path) -> Path:
    """调 SAPI 合成语音，返回原始 WAV 路径（未重采样）。"""
    text_file = work_dir / f"{Path(clip.name).stem}.txt"
    text_file.write_text(clip.text, encoding="utf-8")
    raw_wav = work_dir / f"{Path(clip.name).stem}.raw.wav"
    if raw_wav.exists():
        raw_wav.unlink()
    _run(
        [
            which("pwsh"),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SAPI_TTS),
            "-Voice",
            clip.voice,
            "-TextFile",
            str(text_file),
            "-OutFile",
            str(raw_wav),
            "-Rate",
            str(clip.rate),
        ]
    )
    if not raw_wav.exists() or raw_wav.stat().st_size == 0:
        raise RuntimeError(f"SAPI 未产出音频: {raw_wav}")
    return raw_wav


def to_16k_mono(src: Path, dst: Path) -> None:
    """重采样到 16 kHz mono PCM16（ASR 引擎输入规格）。"""
    _run(
        [
            which("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            str(CHANNELS),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )


def add_noise(src: Path, dst: Path, level: float) -> None:
    """加粉红噪声 + 低通，模拟轻度环境噪声的直播间/户外录音。"""
    dur = probe_duration(src)
    _run(
        [
            which("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color=pink:amplitude=0.35:sample_rate={SAMPLE_RATE}:duration={dur}:seed={NOISE_SEED}",
            "-filter_complex",
            (
                f"[1:a]atrim=0:{dur},asetpts=N/SR/TB[n];"
                f"[0:a][n]amix=inputs=2:duration=first:weights=1 {level},"
                "lowpass=f=4200,"
                "afade=t=in:st=0:d=0.25,"
                f"afade=t=out:st={max(0.0, dur - 0.25):.2f}:d=0.25"
            ),
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            str(CHANNELS),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )


def make_harsh(
    src: Path,
    dst: Path,
    level: float = 0.7,
    lowpass_hz: int = 2200,
) -> None:
    """重度退化：强噪声 + 低通（手机/会议录音频响）+ 混响 + 轻微压缩。

    目的：纯 TTS 语音对 ASR 过于友好（干净音频错误率 0%，无法区分引擎）。
    这条链路把音频推向真实短视频/直播间的录音条件，让错误率落到
    有区分度的区间（预期个位数到十几个百分点）。
    """
    dur = probe_duration(src)
    _run(
        [
            which("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color=pink:amplitude=0.5:sample_rate={SAMPLE_RATE}:duration={dur}:seed={NOISE_SEED}",
            "-filter_complex",
            (
                f"[1:a]atrim=0:{dur},asetpts=N/SR/TB[n];"
                f"[0:a][n]amix=inputs=2:duration=first:weights=1 {level},"
                f"lowpass=f={lowpass_hz},"
                "aecho=0.8:0.85:400|600:0.25|0.18,"
                "acompressor=threshold=0.1:ratio=4:attack=5:release=50,"
                "afade=t=in:st=0:d=0.2,"
                f"afade=t=out:st={max(0.0, dur - 0.2):.2f}:d=0.2"
            ),
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            str(CHANNELS),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Generate real (offline TTS) benchmark corpus")
    p.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "output" / "bench" / "corpus",
        help="Output directory (default: output/bench/corpus)",
    )
    p.add_argument(
        "--work",
        type=Path,
        default=None,
        help="Scratch dir for raw TTS output (default: <out>/.work)",
    )
    p.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Corpus manifest path (default: <out>/corpus_real.json)",
    )
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip clips whose WAV already exists (default: regenerate)",
    )
    args = p.parse_args(argv)

    out_dir = args.out
    work_dir = args.work or (out_dir / ".work")
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = args.manifest or (out_dir / "corpus_real.json")
    files: List[Dict] = []

    for clip in CLIPS:
        wav_path = out_dir / clip.name
        was_skipped = False
        if args.skip_existing and wav_path.exists():
            print(f"[skip] {wav_path.name} (exists)")
            duration = probe_duration(wav_path)
            was_skipped = True
        else:
            print(f"[tts ] {clip.name} <- {clip.voice}")
            raw = synthesize(clip, work_dir)
            to_16k_mono(raw, wav_path)
            duration = probe_duration(wav_path)
            print(f"       -> {duration:.1f}s  {wav_path.stat().st_size / 1024:.0f} KiB")

        ref = build_reference(clip, wav_path, duration)
        ref_path = out_dir / (Path(clip.name).stem + ".json")
        ref_path.write_text(json.dumps(ref, ensure_ascii=False, indent=2), encoding="utf-8")

        files.append(
            {
                "name": clip.name,
                "language": clip.language,
                "duration_s": duration,
                "speakers": clip.speakers,
                "topic": clip.topic,
                "voice": clip.voice,
                "reference": ref_path.name,
            }
        )

        # 重度退化变体
        # 注意：--skip-existing 时**不能**重复做降级，否则已降级的音频会被
        # 二次处理，语料难度悄悄漂移（基准就不可比了）。
        if clip.harsh and not was_skipped:
            print(f"[harsh] {clip.name} (noise={clip.harsh_noise} lowpass={clip.harsh_lowpass_hz}Hz)")
            # ffmpeg 不能原地读写同一个文件 —— 先写临时文件再替换
            tmp_wav = wav_path.with_name(wav_path.stem + ".harsh.tmp.wav")
            make_harsh(
                wav_path, tmp_wav, level=clip.harsh_noise, lowpass_hz=clip.harsh_lowpass_hz
            )
            tmp_wav.replace(wav_path)
            duration = probe_duration(wav_path)
            ref["duration_s"] = duration
            ref["topic"] = clip.topic
            ref_path.write_text(json.dumps(ref, ensure_ascii=False, indent=2), encoding="utf-8")
            files[-1]["duration_s"] = duration
            files[-1]["topic"] = clip.topic

        # 噪声变体
        if clip.noisy_of == clip.name:
            noisy_name = clip.name.replace(".wav", "_noisy.wav")
            noisy_path = out_dir / noisy_name
            if not (args.skip_existing and noisy_path.exists()):
                print(f"[noise] {noisy_name} (level={clip.noise_level})")
                add_noise(wav_path, noisy_path, clip.noise_level)
            noisy_dur = probe_duration(noisy_path)
            noisy_ref = build_reference(clip, noisy_path, noisy_dur)
            noisy_ref["file"] = noisy_name
            noisy_ref["topic"] = clip.topic + "（轻度环境噪声变体）"
            noisy_ref["noise_level"] = clip.noise_level
            (out_dir / (Path(noisy_name).stem + ".json")).write_text(
                json.dumps(noisy_ref, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            files.append(
                {
                    "name": noisy_name,
                    "language": clip.language,
                    "duration_s": noisy_dur,
                    "speakers": clip.speakers,
                    "topic": clip.topic + "（轻度环境噪声变体）",
                    "voice": clip.voice,
                    "reference": Path(noisy_name).stem + ".json",
                }
            )

    manifest = {
        "schema_version": CORPUS_SCHEMA,
        "description": (
            "Real-speech corpus synthesised offline via Windows SAPI. "
            "16 kHz mono PCM16. Reference text exact by construction."
        ),
        "generator": "scripts/benchmark_corpus/gen_corpus_real.py",
        "language_codes": {
            "zh": "Mandarin (普通话)",
            "en": "English (英语)",
            "mixed": "中英混说",
        },
        "files": files,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    total = sum(f["duration_s"] for f in files)
    print(f"\n[ok] {len(files)} clips, total {total / 60:.1f} min -> {out_dir}")
    print(f"[ok] manifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
