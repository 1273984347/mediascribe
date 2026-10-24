"""Capture CLI help output to text snapshots.

This is the canonical way to refresh the files in
``docs/cli-screenshots/``. Run it whenever the CLI surface changes:

    python scripts/e2e/capture_cli_help.py

It writes:
    docs/cli-screenshots/mediascribe-help.txt
    docs/cli-screenshots/mediascribe-mcp-help.txt
    docs/cli-screenshots/archive-help.txt
    docs/cli-screenshots/archive-help-zh.txt
    docs/cli-screenshots/archive-help-en.txt
    docs/cli-screenshots/index.md
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).parent.parent.parent
OUT = ROOT / "docs" / "cli-screenshots"

COMMANDS = [
    (
        "mediascribe-help.txt",
        [sys.executable, "-m", "mediascribe", "--help"],
        "Main mediascribe CLI help (English)",
    ),
    (
        "mediascribe-mcp-help.txt",
        [sys.executable, "-m", "mediascribe.mcp_server", "--help"],
        "MCP server CLI options",
    ),
    (
        "archive-help.txt",
        [sys.executable, "-m", "mediascribe.batch_cli", "--help"],
        "batch CLI / mediascribe archive (auto-detect language)",
    ),
    (
        "archive-help-zh.txt",
        [sys.executable, "-m", "mediascribe.batch_cli", "--help", "--lang", "zh"],
        "batch CLI forced to Chinese",
    ),
    (
        "archive-help-en.txt",
        [sys.executable, "-m", "mediascribe.batch_cli", "--help", "--lang", "en"],
        "batch CLI forced to English",
    ),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for filename, cmd, desc in COMMANDS:
        print(f"--- {filename} ---")
        try:
            r = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=15,
            )
            # argparse writes --help to stdout; some legacy CLIs use stderr.
            text = (r.stdout or "") + (r.stderr or "")
            if not text.strip():
                text = f"(no output, exit={r.returncode})"
        except subprocess.TimeoutExpired:
            text = "(timeout)"
        except FileNotFoundError as e:
            text = f"(error: {e})"
        (OUT / filename).write_text(text, encoding="utf-8")
        results.append((filename, len(text), desc))
        print(f"  wrote {len(text)} chars")

    # Render markdown index
    lines = [
        "# CLI Screenshots",
        "",
        "Auto-generated text snapshots of CLI help output. Useful for",
        "documentation sites and for verifying that flags haven't drifted",
        "between releases.",
        "",
        "| File | Bytes | Description |",
        "|------|------:|-------------|",
    ]
    for fn, n, desc in results:
        lines.append(f"| `{fn}` | {n} | {desc} |")
    lines.extend(
        [
            "",
            "## How to regenerate",
            "",
            "```bash",
            "python scripts/e2e/capture_cli_help.py",
            "```",
            "",
        ]
    )
    (OUT / "index.md").write_text("\n".join(lines), encoding="utf-8")
    print("\nWrote: docs/cli-screenshots/index.md")


if __name__ == "__main__":
    main()
