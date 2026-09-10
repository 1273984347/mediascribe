# CLI Screenshots

Auto-generated text snapshots of CLI help output. These are useful for
documentation sites and for verifying that flags haven't drifted between
releases.

| File | Bytes | Description |
|------|------:|-------------|
| `mediascribe-help.txt` | 1551 | Main mediascribe CLI help (English) |
| `mediascribe-mcp-help.txt` | 342 | MCP server CLI options |
| `douyin-batch-v3-help.txt` | 1964 | douyin_batch_v3 (auto-detect language) |
| `douyin-batch-v3-help-zh.txt` | 1964 | douyin_batch_v3 forced to Chinese |
| `douyin-batch-v3-help-en.txt` | 2664 | douyin_batch_v3 forced to English |

## How to regenerate

```bash
python scripts/capture_cli_help.py
```

## `mediascribe-help.txt`

```text
usage: __main__.py [-h] [--workspace WORKSPACE]
                   [--model {tiny,base,small,medium,large}]
                   [--device {cpu,cuda}]
                   [--engine {whisper,whisperx,faster-whisper}]
                   [--language LANGUAGE]
                   {transcribe,t,batch} ...

🎬 MediaScribe - 视频转文字工具（深度整合版）

options:
  -h, --help            show this help message and exit
  --workspace WORKSPACE, -w WORKSPACE
                        工作目录（默认: ./output）
  --model {tiny,base,small,medium,large}, -m {tiny,base,small,medium,large}
                        Whisper 模型（默认: small）
  --device {cpu,cuda}, -d {cpu,cuda}
                        运行设备（默认: 自动检测）
  --engine {whisper,whisperx,faster-whisper}, -e {whisper,whisperx,faster-whisper}
                        转录引擎（默认: whisper）
  --language LANGUAGE, -l LANGUAGE
                        语言代码（如: zh, en, ja）

命令:
  {transcribe,t,batch}
    transcribe (t)      转录单个视频/音频
    batch               批量处理多个输入

📦 整合项目：
  - yt-dlp: 多平台视频下载
  - bili2text: 优雅的工作流设计
  - WhisperX: 说话人分离 + Word-level 对齐
  - faster-whisper: whisper.cpp 的高性能 Python 版本

🚀 使用示例：
  # 基本使用（Whisper）
  python -m mediascribe transcribe video.mp4
  python -m mediascribe transcribe https://www.bilibili.com/video/BV...

  # 使用 WhisperX + 说话人分离
  python -m mediascribe transcribe video.mp4 --engine whisperx --diarization --hf-token YOUR_TOKEN

  # 使用 faster-whisper（更快）
  python -m mediascribe transcribe video.mp4 --engine faster-whisper

  # 批量处理
  python -m mediascribe batch video1.mp4 video2.mp4 https://...
        

```

## `mediascribe-mcp-help.txt`

```text
usage: mcp_server.py [-h] [--transport {stdio,http}] [--host HOST]
                     [--port PORT]

MediaScribe MCP server — expose transcription as MCP tools

options:
  -h, --help            show this help message and exit
  --transport {stdio,http}
                        Transport protocol (default: stdio)
  --host HOST
  --port PORT

```
