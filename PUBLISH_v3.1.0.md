# Publishing v3.1.0 to GitHub

The local git repository and the `v3.1.0` annotated tag are already in
place at `d:\1\mediascribe`. To publish, follow the three steps below.

## 1. Create the remote repository

On GitHub:

1. Go to https://github.com/new
2. **Repository name**: `mediascribe`
3. **Description**: `Offline video transcription. Download from Bilibili,
   Douyin, YouTube, Xiaohongshu, WeChat MP and transcribe locally with
   Whisper / WhisperX / faster-whisper.`
4. **Visibility**: Public (or Private if you prefer)
5. **Initialize with**: nothing — we already have a local repo
6. Click **Create repository**

## 2. Add the remote and push

```bash
cd d:\1\mediascribe
git remote add origin https://github.com/<your-org>/mediascribe.git
git push -u origin main
git push origin v3.1.0
```

The push will create the tag `v3.1.0` on the remote.

## 3. Create the GitHub Release

You have two options. Pick whichever is more convenient.

### Option A — via the web UI

1. Go to https://github.com/<your-org>/mediascribe/releases/new
2. **Choose the tag**: `v3.1.0`
3. **Release title**: `v3.1.0`
4. **Description**: paste the full contents of
   [`GITHUB_RELEASE_v3.1.0.md`](./GITHUB_RELEASE_v3.1.0.md)
5. Optionally attach build artifacts (Docker image, browser extension ZIP)
6. Click **Publish release**

### Option B — via the GitHub CLI

```bash
gh release create v3.1.0 \
  --title "v3.1.0" \
  --notes-file GITHUB_RELEASE_v3.1.0.md
```

The tag already exists locally, so `gh` will reuse it.

## Optional: attach the browser extension ZIP

The extension ZIP can be built by the Web UI and uploaded as a release
asset. To build it headlessly, you can also use the CLI:

```bash
python -m mediascribe.web.extension_builder --output extension.zip
gh release upload v3.1.0 extension.zip
```

## What if the local tag gets out of date?

If you make changes and want to re-tag:

```bash
# delete the local tag
git tag -d v3.1.0
# re-create against the new commit
git tag -a v3.1.0 -m "Release v3.1.0 ..."
# delete the remote tag (if already pushed)
git push origin :refs/tags/v3.1.0
# re-push
git push origin v3.1.0
```
