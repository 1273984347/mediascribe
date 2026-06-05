"""Plugin system: third-party extension points for Video2Text.

Public API
----------
* :func:`get_downloader` — look up a downloader by name
* :func:`get_transcriber_cls` — look up a transcriber by name
* :func:`get_url_transformer` — look up a URL transformer
* :func:`list_downloaders` / :func:`list_transcribers` / :func:`list_url_transformers`
* :func:`clear_cache` — forget cached entry points (for tests)
* :class:`DownloaderHookSpec` / :class:`TranscriberHookSpec` / :class:`URLTransformerHookSpec`
"""
from .registry import (
    ENTRY_POINT_GROUPS,
    DownloaderHookSpec,
    TranscriberHookSpec,
    URLTransformerHookSpec,
    clear_cache,
    get_downloader,
    get_transcriber_cls,
    get_url_transformer,
    iter_url_transformers,
    list_downloaders,
    list_transcribers,
    list_url_transformers,
)

__all__ = [
    "ENTRY_POINT_GROUPS",
    "DownloaderHookSpec",
    "TranscriberHookSpec",
    "URLTransformerHookSpec",
    "get_downloader",
    "get_transcriber_cls",
    "get_url_transformer",
    "iter_url_transformers",
    "list_downloaders",
    "list_transcribers",
    "list_url_transformers",
    "clear_cache",
]
