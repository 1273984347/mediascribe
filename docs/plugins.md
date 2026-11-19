# Plugins

Third-party packages can extend MediaScribe via the standard
`entry_points` mechanism.  Three groups are recognised:

| Group | What to register | Used by |
|-------|------------------|---------|
| `mediascribe.downloaders` | a class subclassing `Downloader` | `parse_source` |
| `mediascribe.transcribers` | a class subclassing `Transcriber` | `transcriber_factory` |
| `mediascribe.url_transformers` | a callable `(url) -> Optional[str]` | URL canonicalisation |

## Registering a downloader

In your package's `pyproject.toml`:

```toml
[project.entry-points."mediascribe.downloaders"]
mysite = "my_pkg:MysiteDownloader"
```

Implement the hookspec:

```python
from mediascribe.downloaders.base import Downloader
from mediascribe.models import SourceRef, DownloadResult

class MysiteDownloader(Downloader):
    name = "mysite"

    def supports(self, source: SourceRef) -> bool:
        return "mysite.com" in (source.url or "")

    def download(self, source, settings, **kwargs) -> DownloadResult:
        # ... fetch the media ...
        return DownloadResult(source=source, video_path=path, ...)
```

After `pip install my-pkg`, the new downloader is auto-registered.
No change to MediaScribe is required.

## Registering a transcriber

Same pattern, under the `mediascribe.transcribers` group:

```toml
[project.entry-points."mediascribe.transcribers"]
my_asr = "my_pkg:MyAsrTranscriber"
```

```python
from mediascribe.transcribers.base import Transcriber


class MyAsrTranscriber(Transcriber):
    name = "my_asr"

    def transcribe(self, audio, output, *, language=None, **kw):
        # ... run your model ...
        Path(output).write_text(result_text, encoding="utf-8")
        return MyResult(text=result_text, segments=segments)
```

## URL transformers

A URL transformer is a plain function:

```python
def transform(url: str) -> Optional[str]:
    if "myshortener.com" not in url:
        return None
    # ... resolve the redirect ...
    return canonical_url
```

Register it:

```toml
[project.entry-points."mediascribe.url_transformers"]
myshort = "my_pkg:transform"
```

## Discovery

Use `mediascribe.plugins` to inspect what's installed:

```python
from mediascribe import plugins

print(plugins.list_downloaders())  # ['mysite', 'vimeo', ...]
print(plugins.list_transcribers())  # ['my_asr', 'whisper', ...]
print(plugins.list_url_transformers())
```

`plugins.clear_cache()` is provided for tests.

## Example plugins (reference code only)

`examples/plugins/` ships two **reference implementations**:

* `vimeo_downloader.py` — a stub downloader.
* `tcn_transformer.py` — a `t.cn` short-link resolver.

They are **NOT registered by default** (2026-11-20 review P2-3:
sample code should not sit on the production discovery path).  To
activate one, copy it into your own package and declare the
entry-point yourself:

```toml
[project.entry-points."mediascribe.downloaders"]
vimeo = "my_pkg.vimeo_downloader:VimeoDownloader"
```
