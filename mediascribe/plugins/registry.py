"""
Plugin registry for MediaScribe.

Third-party packages can extend the project by registering
``Downloader``, ``Transcriber``, or ``URLTransformer`` plugins via
the standard ``entry_points`` mechanism.

In your ``pyproject.toml``::

    [project.entry-points."mediascribe.downloaders"]
    mysite = "my_pkg:MysiteDownloader"

    [project.entry-points."mediascribe.transcribers"]
    my_asr = "my_pkg:MyAsrTranscriber"

    [project.entry-points."mediascribe.url_transformers"]
    shortlink = "my_pkg:MyShortLinkResolver"

After ``pip install my-pkg`` the registry picks them up
automatically — no code change in mediascribe is required.

The discovery mechanism uses ``importlib.metadata`` (Python 3.8+),
which is the modern, recommended replacement for
``pkg_resources``.  Lookups are cached in the module-level
``_CACHE`` dict so repeated calls are O(1).
"""
from __future__ import annotations

import importlib
import importlib.metadata
from typing import Any, Dict, Iterable, List, Optional, Type

from ..downloaders.base import Downloader
from ..transcribers.base import Transcriber

ENTRY_POINT_GROUPS = {
    "downloaders": "mediascribe.downloaders",
    "transcribers": "mediascribe.transcribers",
    "url_transformers": "mediascribe.url_transformers",
}


_CACHE: Dict[str, Dict[str, Any]] = {group: {} for group in ENTRY_POINT_GROUPS}


def _load_entry_points(group: str) -> Dict[str, Any]:
    """Discover and load all entry points for ``group`` (cached)."""
    if group in _CACHE and _CACHE[group]:
        return _CACHE[group]
    eps = importlib.metadata.entry_points()
    # Python 3.10+ returns a ``SelectableGroups`` view; older
    # versions return a dict of lists.  Handle both.
    if hasattr(eps, "select"):
        matches = list(eps.select(group=group))
    elif isinstance(eps, dict):
        matches = eps.get(group, [])
    else:  # pragma: no cover - very old pkg_resources
        matches = [ep for ep in eps if ep.group == group]
    out: Dict[str, Any] = {}
    for ep in matches:
        try:
            loaded = ep.load()
            out[ep.name] = loaded
        except Exception as exc:  # pragma: no cover - defensive
            # Never let one bad plugin break the whole registry.
            out[ep.name] = exc
    _CACHE[group] = out
    return out


def clear_cache() -> None:
    """Forget all cached entry points.  Useful in tests."""
    for k in _CACHE:
        _CACHE[k] = {}


def get_downloader(name: str) -> Optional[Type[Downloader]]:
    """Look up a downloader class by name.  Returns the class, not an instance."""
    group = ENTRY_POINT_GROUPS["downloaders"]
    eps = _load_entry_points(group)
    cls = eps.get(name)
    if cls is None or isinstance(cls, Exception):
        return None
    return cls


def get_transcriber_cls(name: str) -> Optional[Type[Transcriber]]:
    """Look up a transcriber class by name."""
    group = ENTRY_POINT_GROUPS["transcribers"]
    eps = _load_entry_points(group)
    cls = eps.get(name)
    if cls is None or isinstance(cls, Exception):
        return None
    return cls


def get_url_transformer(name: str) -> Optional[Any]:
    """Look up a URL transformer (callable or class) by name."""
    group = ENTRY_POINT_GROUPS["url_transformers"]
    eps = _load_entry_points(group)
    return eps.get(name)


def list_downloaders() -> List[str]:
    """Names of all registered downloaders (built-in + plugins)."""
    return sorted(_load_entry_points(ENTRY_POINT_GROUPS["downloaders"]).keys())


def list_transcribers() -> List[str]:
    """Names of all registered transcribers (built-in + plugins)."""
    return sorted(_load_entry_points(ENTRY_POINT_GROUPS["transcribers"]).keys())


def list_url_transformers() -> List[str]:
    """Names of all registered URL transformers."""
    return sorted(_load_entry_points(ENTRY_POINT_GROUPS["url_transformers"]).keys())


# ---------------------------------------------------------------------------
# Hookspecs: the contract plugins can implement
# ---------------------------------------------------------------------------
class DownloaderHookSpec:
    """Mixin documenting the methods a downloader plugin may implement.

    MediaScribe does NOT enforce an abstract base class on plugin
    downloaders — any class with a ``supports(source)`` and
    ``download(source, settings, **kwargs)`` method works.  This
    class is just a typed hint for IDE auto-completion.
    """

    name: str = ""

    def supports(self, source: Any) -> bool:
        """Return True if this downloader handles ``source``."""
        raise NotImplementedError

    def download(self, source: Any, settings: Any, **kwargs: Any) -> Any:
        """Download the media and return a ``DownloadResult``."""
        raise NotImplementedError


class TranscriberHookSpec:
    """Mixin for plugin transcribers."""

    name: str = ""

    def transcribe(self, audio_or_video_path: str, output_path: str,
                   *, language: Optional[str] = None, **kwargs: Any) -> Any:
        """Transcribe and write the result to ``output_path``."""
        raise NotImplementedError


class URLTransformerHookSpec:
    """A URL transformer resolves a raw URL into a canonical form.

    Plugins implementing this spec should expose a callable:

        def transform(url: str) -> Optional[str]:
            '''Return the canonical URL, or None to skip.'''
    """
    name: str = ""

    def transform(self, url: str) -> Optional[str]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Internal helpers (used by the Pipeline at runtime)
# ---------------------------------------------------------------------------
def iter_url_transformers() -> Iterable[Any]:
    """Yield all loaded URL transformer classes in registration order."""
    group = ENTRY_POINT_GROUPS["url_transformers"]
    yield from _load_entry_points(group).values()
