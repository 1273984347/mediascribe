"""徐涛课程批量转录(通用版)

用法:
    python scripts/batch_transcribe.py --bv BV1CE411o7x3 --total 8
    python scripts/batch_transcribe.py --bv BV15ocBzQEJJ --total 37

- 引擎: faster-whisper large-v3 + CUDA + 中文, 带时间戳, --audio-only
- 防呆: 启动前拉取全部分 P 的 id, 校验数量/唯一性/与序号对应
  (曾发生过 URL ?p=N 被剥掉导致 37 集全变 P1 的事故, 此断言防复发)
- 断点续跑: output/.batch_state_<BV>.json 记录已完成集数
  (转录稿文件名不含分P号, 以状态文件 + 文件仍存在为准)
- 退出码 0 但没有新产物视为失败(静默重复转录同一集的事故模式)
- 单集失败不中断整体, 退出码非 0 表示有失败集数, 日志见 stdout
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "transcripts"


def load_state(state_path: Path) -> dict:
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch_part_titles(bv: str) -> list:
    """拉取全部分 P 的 (playlist_index, title)。

    注意: flat-playlist 模式下 B 站不返回 entry 的 title(为 "NA"),
    ``%(id)s`` 也不含 ``_pN`` 后缀; 分 P 身份只能靠 playlist_index。
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--flat-playlist",
            "--print",
            "%(playlist_index)s|%(title)s",
            f"https://www.bilibili.com/video/{bv}/",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    parts = []
    for line in (result.stdout or "").splitlines():
        if "|" not in line:
            continue
        idx, title = line.split("|", 1)
        if idx.strip().isdigit():
            parts.append((int(idx), title.strip()))
    return parts


def preflight(bv: str, total: int) -> int:
    """防呆断言: 序号连续且有序(防多 P 元数据/URL 解析异常)。返回实际可转集数。"""
    parts = fetch_part_titles(bv)
    if not parts:
        raise RuntimeError(f"未能拉取分 P 列表: {bv}")
    indexes = [i for i, _ in parts]
    if indexes != list(range(1, len(indexes) + 1)):
        raise RuntimeError(
            f"分 P 序号异常(应连续 1..N): {indexes[:10]}... 多 P 识别可能有问题, 中止"
        )
    if len(parts) < total:
        print(f"[warn] 预期 {total} 集, 实际只有 {len(parts)} 集, 按实际数执行", flush=True)
        return len(parts)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="B 站多 P 课程批量转录")
    parser.add_argument("--bv", required=True, help="视频 BV 号")
    parser.add_argument("--total", type=int, required=True, help="分 P 总数")
    parser.add_argument("--state", default=None, help="断点文件名(默认 .batch_state_<BV>.json)")
    args = parser.parse_args()

    print("[preflight] 校验分 P 列表...", flush=True)
    total = preflight(args.bv, args.total)

    state_path = ROOT / "output" / (args.state or f".batch_state_{args.bv}.json")
    state = load_state(state_path)
    failed = []
    for p in range(1, total + 1):
        record = state.get(str(p))
        if record and (OUT / record).exists():
            print(f"[skip] p{p} 已有转录稿 {record}", flush=True)
            continue
        url = f"https://www.bilibili.com/video/{args.bv}?p={p}"
        before = {f.name for f in OUT.glob("*.md")} if OUT.exists() else set()
        print(f"[run ] p{p} {url}", flush=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mediascribe",
                "transcribe",
                url,
                "--engine",
                "faster-whisper",
                "--device",
                "cuda",
                "--language",
                "zh",
                "--model",
                "large-v3",
                "--timestamps",
                "--audio-only",
            ],
            cwd=ROOT,
        )
        print(f"[exit] p{p} -> {result.returncode}", flush=True)
        after = {f.name for f in OUT.glob("*.md")} if OUT.exists() else set()
        new_files = sorted(after - before)
        if result.returncode != 0 or not new_files:
            # 退出码 0 但无新产物 = 可能重复转录了同一集(事故模式), 按失败处理
            failed.append(p)
            print(f"[fail] p{p} 退出码 {result.returncode}, 新产物 {new_files or '无'}", flush=True)
            time.sleep(3)
            continue
        state[str(p)] = new_files[0]
        save_state(state_path, state)
        time.sleep(3)  # 降低对 B 站接口的请求频率
    if failed:
        print(f"FAILED PARTS: {failed}", flush=True)
        return 1
    print("ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
