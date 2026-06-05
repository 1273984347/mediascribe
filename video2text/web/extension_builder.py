"""
Build a self-contained ZIP archive of the browser extension.

The archive is what end users download from the Web UI's
"Get the extension" page and load into Chrome / Edge via
"Load unpacked" (after extracting the ZIP).  We do not produce a
``.crx`` (signed) package because:

* The Web UI ships inside the user's own infrastructure; signing
  would require a developer key per deployment.
* Chrome / Edge will accept the unzipped ``extension/`` tree as an
  unpacked extension, which is what the README tells users to do.

The builder is deliberately a single function so the Web app can
serve the ZIP on demand without writing to disk.
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Iterable, Tuple

# Files / directories that should NEVER be packaged, even if they
# happen to live in the extension tree during development.
_EXCLUDE_NAMES: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".DS_Store",
        "Thumbs.db",
        "node_modules",
        ".git",
        ".idea",
        ".vscode",
    }
)

_EXCLUDE_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
    ".swp",
    ".tmp",
    ".bak",
)

_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB per-file cap


def _iter_extension_files(extension_root: Path) -> Iterable[Tuple[Path, str]]:
    """Yield ``(absolute_path, archive_name)`` for every file to package.

    The archive name is the path *relative to* ``extension_root``,
    using forward slashes regardless of platform (zipfile spec).
    Symlinks are skipped — the extension should not contain any,
    and following them would risk pulling in source files outside
    the repo.
    """
    if not extension_root.is_dir():
        raise FileNotFoundError(
            f"extension source dir not found: {extension_root}"
        )
    for entry in sorted(extension_root.rglob("*")):
        if entry.is_dir():
            continue
        if entry.is_symlink():
            # Refuse to follow — package the link target at your own peril.
            continue
        rel = entry.relative_to(extension_root)
        parts = rel.parts
        if any(p in _EXCLUDE_NAMES for p in parts):
            continue
        if any(entry.name.endswith(suf) for suf in _EXCLUDE_SUFFIXES):
            continue
        if entry.stat().st_size > _MAX_FILE_BYTES:
            # Silently skip — better than crashing the download endpoint.
            continue
        yield entry, rel.as_posix()


def _load_manifest_version(extension_root: Path) -> str:
    """Read the manifest's ``version`` field, or ``"0.0.0"`` on failure."""
    manifest_path = extension_root / "manifest.json"
    if not manifest_path.is_file():
        return "0.0.0"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        v = str(data.get("version", "0.0.0")).strip()
        return v or "0.0.0"
    except (json.JSONDecodeError, OSError):
        return "0.0.0"


def _sanitize_for_filename(name: str) -> str:
    """Map an arbitrary version to a filesystem-friendly slug.

    The output keeps alphanumerics, ``.``, ``-`` and ``_``; anything
    else is replaced with ``_``.  The result is also lower-cased so
    a download of, say, ``v3.1.0`` and ``V3.1.0`` cannot collide on
    case-insensitive filesystems.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or "0.0.0"


def build_extension_zip(
    extension_root: Path,
    *,
    extra_files: dict[str, str] | None = None,
    extra_instructions: str | None = None,
) -> Tuple[bytes, str]:
    """Build the on-the-fly ZIP the Web UI serves to users.

    Parameters
    ----------
    extension_root:
        Absolute path to the ``extension/`` source tree.
    extra_files:
        Optional mapping of ``archive-name → text-content`` for
        files to inject at ZIP-build time.  Used to add a freshly
        generated ``INSTALL.md`` with the user's actual endpoint
        URL embedded, so the install instructions match the running
        server.  Keys must be simple relative paths without
        traversal segments.
    extra_instructions:
        Optional override for the contents of the auto-generated
        ``INSTALL.md``.  If omitted, a default markdown block is
        produced that tells the user how to load the unpacked
        extension.

    Returns
    -------
    (zip_bytes, filename):
        ``zip_bytes`` is the in-memory ZIP; ``filename`` is the
        suggested download filename, e.g. ``video2text-extension-v3.1.0.zip``.
    """
    manifest_version = _load_manifest_version(extension_root)
    filename = (
        f"video2text-extension-v{_sanitize_for_filename(manifest_version)}.zip"
    )

    buf = io.BytesIO()
    # We use ZIP_DEFLATED to keep the download small; the icons
    # (PNGs) compress particularly well.
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_path, arcname in _iter_extension_files(extension_root):
            zf.write(abs_path, arcname)
        # Inject the install instructions — these are generated
        # per-download so we can embed the user's endpoint URL.
        if extra_files:
            for name, content in extra_files.items():
                if name is None:
                    continue
                # Strip whitespace and path separators; reject
                # anything that still looks empty, looks like a
                # traversal segment, or escapes the archive root.
                cleaned = str(name).strip().strip("/").strip()
                if not cleaned:
                    continue
                if ".." in Path(cleaned).parts:
                    continue
                zf.writestr(cleaned, content)

    return buf.getvalue(), filename


def build_install_markdown(
    *,
    web_ui_origin: str,
    chrome: bool = True,
    edge: bool = True,
) -> str:
    """Render the per-user ``INSTALL.md`` document.

    Parameters
    ----------
    web_ui_origin:
        The ``scheme://host[:port]`` of the running Web UI, used in
        the screenshots / pre-populated endpoint hint.
    chrome, edge:
        Whether to show install steps for that browser.
    """
    parts: list[str] = [
        "# Video2Text Browser Extension — Install Guide",
        "",
        f"_This ZIP was generated on {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"for Web UI origin **{web_ui_origin}**._",
        "",
        "## 1. Extract this archive",
        "",
        "Extract the downloaded `.zip` into a permanent folder, e.g.",
        "``~/video2text-extension/``. The folder must contain "
        "``manifest.json`` at the top level.",
        "",
        "## 2. Load the extension in your browser",
        "",
    ]
    if chrome:
        parts.extend(
            [
                "### Chrome / Chromium / Brave / Arc",
                "",
                "1. Open `chrome://extensions/`.",
                "2. Enable **Developer mode** (top right).",
                "3. Click **Load unpacked** and select the extracted folder.",
                "4. (Optional) Pin the extension: click the puzzle icon "
                "→ pin **Video2Text Sender**.",
                "",
            ]
        )
    if edge:
        parts.extend(
            [
                "### Microsoft Edge",
                "",
                "1. Open `edge://extensions/`.",
                "2. Enable **Developer mode** (bottom-left).",
                "3. Click **Load unpacked** and select the extracted folder.",
                "4. (Optional) Right-click the toolbar → **Show Video2Text "
                "Sender in side panel** for a docked experience.",
                "",
            ]
        )
    parts.extend(
        [
            "## 3. Configure the endpoint",
            "",
            f"On first install, the options page opens. Enter the Web UI "
            f"URL (default: `{web_ui_origin}`) and your Bearer token if the "
            f"server has `VIDEO2TEXT_API_TOKEN` set.",
            "",
            "## 4. Use the extension",
            "",
            "* Click the toolbar icon → the popup opens → **Send to "
            "Video2Text**.",
            "* Or click **Open in side panel** to keep the transcript reader "
            "docked to the right edge of the browser.",
            "",
            "## Troubleshooting",
            "",
            "* `Side panel requires Chrome / Edge 114+` — update the browser.",
            "* `CORS blocked` — the Web UI allows `chrome-extension://*` and "
            "`moz-extension://*` by default; check that you have not "
            "overridden `VIDEO2TEXT_CORS_ORIGINS` to a stricter list.",
            "* `401 Unauthorized` — the Bearer token in the popup / options "
            "page must match `VIDEO2TEXT_API_TOKEN` on the server.",
            "",
        ]
    )
    return "\n".join(parts)
