# Plugins

Third-party packages can extend Video2Text via the standard
`entry_points` mechanism.  Three groups are recognised:

| Group | What to register | Used by |
|-------|------------------|---------|
| `video2text.downloaders` | a class subclassing `Downloader` | `parse_source` |
| `video2text.transcribers` | a class subclassing `Transcriber` | `transcriber_factory` |
| `video2text.url_transformers` | a callable `(url) -> Optional[str]` | URL canonicalisation |

## Registering a downloader

In your package's `pyproject.toml`:

```toml
[project.entry-points."video2text.downloaders"]
mysite = "my_pkg:MysiteDownloader"
```

Implement the hookspec:

```python
from video2text.downloaders.base import Downloader
from video2text.models import SourceRef, DownloadResult

class MysiteDownloader(Downloader):
    name = "mysite"

    def supports(self, source: SourceRef) -> bool:
        return "mysite.com" in (source.url or "")

    def download(self, source, settings, **kwargs) -> DownloadResult:
        # ... fetch the media ...
        return DownloadResult(source=source, video_path=path, ...)
```

After `pip install my-pkg`, the new downloader is auto-registered.
No change to Video2Text is required.

## Registering a transcriber

Same pattern, under the `video2text.transcribers` group:

```toml
[project.entry-points."video2text.transcribers"]
my_asr = "my_pkg:MyAsrTranscriber"
```

```python
from video2text.transcribers.base import Transcriber

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
[project.entry-points."video2text.url_transformers"]
myshort = "my_pkg:transform"
```

## Discovery

Use `video2text.plugins` to inspect what's installed:

```python
from video2text import plugins
print(plugins.list_downloaders())   # ['mysite', 'vimeo', ...]
print(plugins.list_transcribers())  # ['my_asr', 'whisper', ...]
print(plugins.list_url_transformers())
```

`plugins.clear_cache()` is provided for tests.

## Example plugin shipped with the project

See `examples/plugins/`:

* `vimeo_downloader.py` — a stub downloader registered in
  `pyproject.toml` so the plugin-system tests can exercise the
  end-to-end flow.
* `tcn_transformer.py` — a `t.cn` short-link resolver.
