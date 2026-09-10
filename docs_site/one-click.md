# One-click Web UI

The fastest way to try MediaScribe without writing code: launch a
local Web UI server, open it in your browser, and load the browser
extension that ships in this repository.  The whole flow is one
command on every operating system.

## TL;DR

=== "Linux / macOS / WSL"

    ```bash
    # Clone, install, run — that's it.
    git clone https://github.com/example/mediascribe.git
    cd mediascribe
    pip install -e ".[web]"
    ./scripts/one_click_up.sh           # foreground
    # or
    ./scripts/one_click_up.sh up --daemon
    ```

=== "Windows (PowerShell)"

    ```powershell
    git clone https://github.com/example/mediascribe.git
    cd mediascribe
    pip install -e ".[web]"
    .\scripts\one_click_up.ps1
    # or
    .\scripts\one_click_up.ps1 up --daemon
    ```

=== "Docker (any OS)"

    ```bash
    git clone https://github.com/example/mediascribe.git
    cd mediascribe
    docker compose up -d --build
    ```

The script then:

1. **Verifies** ffmpeg, fastapi, and a free TCP port.
2. **Starts** `uvicorn mediascribe.web.app:app` (or `docker compose up`).
3. **Polls** `http://127.0.0.1:8000/api/health` for up to 30 seconds.
4. **Opens** the Web UI in your default browser.
5. **Prints** the browser-extension install steps.
6. **Persists** state in `.one-click.pid` + `.one-click.log` so you can
   stop it cleanly with `one_click_up stop`.

## Lifecycle commands

The launcher has three sub-commands, all cross-platform:

| Command | Purpose |
|---------|---------|
| `up` (default) | Start the Web UI.  Foreground by default; add `--daemon` to background. |
| `stop`        | Stop a daemonised server (reads the pid file). |
| `status`      | Print whether the server is up, the pid, and the log path. |

The most common flags:

* `--host 0.0.0.0` — bind on all interfaces (needed for Docker / VM guests).
* `--port 8080` — pick a non-default port.
* `--mode docker` — use `docker compose` instead of a local Python process.
* `--daemon` — run the launcher itself in the foreground but the
  server in the background, then exit.
* `--no-browser` — skip the auto-open step (useful in CI).
* `--reload` — pass `--reload` to uvicorn (dev only).

## Idempotent restart

Calling `up` twice is safe: the launcher checks the pid file and the
TCP port.  If something is already listening on the port, it prints
a clear error and exits 1 instead of starting a duplicate server.

## Where state lives

| File | Purpose |
|------|---------|
| `.one-click.pid` | PID of the running server (or Docker container). |
| `.one-click.log` | Combined stdout/stderr of uvicorn (or `docker compose up`). |
| `web-workspace/`  | (Docker only) Mounted as `/workspace` inside the container. |

Both files live at the project root and are added to `.gitignore`.

## Convenience aliases

### `make`

```makefile
make up            # foreground Web UI
make up-daemon     # background
make down          # stop
make status        # show status
make logs          # tail -f .one-click.log
make docker-up     # docker compose up -d --build
```

### `just`

```just
just up
just up-daemon
just down
just status
just logs
just docker-up
```

### PowerShell on Windows

```powershell
function v2t { & ".\scripts\one_click_up.ps1" @args }
v2t up --daemon
v2t status
v2t down
```

## Loading the browser extension

After the Web UI is up, the launcher prints these steps (also in
``extension/README.md``:

1. Open `chrome://extensions` (or `edge://extensions`).
2. Enable **Developer mode** (top right).
3. Click **Load unpacked** and pick the `extension/` folder of this
   repository.
4. Open the extension's **Options** page and set the API endpoint to
   the URL printed by the launcher (default: `http://127.0.0.1:8000`).
5. Visit any Bilibili / Douyin / YouTube / Xiaohongshu / WeChat MP
   page and click the toolbar icon.

If you launched the Web UI via Docker, the manifest already includes
`http://host.docker.internal/*` in `host_permissions`, so the
extension can reach the host's mapped port from inside the browser.

## Docker specifics

`docker-compose.yml` exposes port 8000, mounts `./web-workspace` into
`/workspace`, and has a healthcheck that polls `/api/health` every
30 seconds.  Transcripts land in `./web-workspace` on the host so
they survive `docker compose down`.

To enable GPU transcription, uncomment the `deploy.resources` block
in `docker-compose.yml` and run on a host with the
[nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
installed.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `port 8000 is already in use` | Another server is bound | `one_click_up stop` (if ours) or pass `--port 8080` |
| `ERROR: fastapi not installed` | `[web]` extra not installed | `pip install "mediascribe[web]"` |
| `ERROR: docker not installed` | Docker daemon missing or PATH | Install Docker Desktop / engine |
| Browser does not open | Headless environment | `one_click_up up --no-browser`, then visit the URL manually |
| `Web UI did not respond within 30s` | Crash before binding | `cat .one-click.log` to see uvicorn's traceback |
| Extension icon greyed out on a page | URL not in supported platforms | See [platforms.md](platforms.md) |

## Why a dedicated launcher and not just `uvicorn`?

`uvicorn mediascribe.web.app:app --reload` works, but you also want:

* **Background / foreground toggle** — Ctrl-C in a terminal kills
  the wrong process when you actually wanted to keep the server
  running.
* **Cross-platform PID tracking** — Windows lacks `kill`, macOS
  lacks `tasklist`, and the path differs in WSL.
* **Pre-flight checks** — missing ffmpeg or fastapi produce clearer
  errors than a stack trace 3 seconds later.
* **Browser-extension hints** — most users forget the endpoint
  step; the launcher prints it for them.
* **Docker parity** — the same flags work for `docker compose`,
  which is the recommended way to deploy on a server.

The launcher is intentionally small (~300 LoC) and depends on the
Python standard library only.
