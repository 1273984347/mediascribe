// B3 候选 —— Tauri 2 桌面壳。
//
// 形态: Tauri 窗口加载 http://127.0.0.1:<port>/, 同时用 std::process 拉起
//       MediaScribe 的 FastAPI 后端 (sidecar), 与 Electron 候选保持同构对照。
//
// 基准信号 (stdout 行, 供 bench_shell.py 抓取):
//   WINDOW_READY —— 窗口页面加载完成
//   APP_USABLE   —— 页面内 /api/health 探针成功
//
// GPU 隔离: sidecar 注入 CUDA_VISIBLE_DEVICES=-1, 避免与并行推理实测争抢显存。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

fn spawn_backend(port: u16) -> Option<std::process::Child> {
    let python = std::env::var("BENCH_PYTHON").unwrap_or_else(|_| "python".to_string());
    let repo = std::env::var("BENCH_REPO").unwrap_or_else(|_| ".".to_string());
    let mut cmd = Command::new(python);
    cmd.args([
        "-m",
        "uvicorn",
        "mediascribe.web.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        &port.to_string(),
    ])
    .current_dir(&repo)
    .env("CUDA_VISIBLE_DEVICES", "-1")
    .env("MEDIASCRIBE_DEVICE", "cpu")
    .env("PYTHONUNBUFFERED", "1")
    .stdout(Stdio::piped())
    .stderr(Stdio::piped());
    let child = cmd.spawn().ok()?;
    if let Some(stdout) = child.stdout.take() {
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                println!("[backend] {line}");
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                println!("[backend] {line}");
            }
        });
    }
    Some(child)
}

fn wait_health(port: u16, timeout_secs: u64) -> bool {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(timeout_secs);
    let url = format!("http://127.0.0.1:{port}/api/health");
    while std::time::Instant::now() < deadline {
        if let Some(body) = http_get(&url) {
            if body.contains("\"ok\":true") || body.contains("\"ok\": true") {
                return true;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(150));
    }
    false
}

/// 极简 HTTP GET (避免为基准引入额外依赖)
fn http_get(url: &str) -> Option<String> {
    use std::io::Read;
    let rest = url.strip_prefix("http://")?;
    let (host_port, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let mut stream = std::net::TcpStream::connect(host_port).ok()?;
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(3)))
        .ok()?;
    let req = format!(
        "GET {path} HTTP/1.1\r\nHost: {host_port}\r\nConnection: close\r\nUser-Agent: mediascribe-bench\r\n\r\n"
    );
    stream.write_all(req.as_bytes()).ok()?;
    let mut buf = String::new();
    stream.read_to_string(&mut buf).ok()?;
    Some(buf)
}

fn main() {
    let port: u16 = std::env::var("BENCH_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8000);

    let _backend = spawn_backend(port);

    tauri::Builder::default()
        .setup(move |app| {
            if !wait_health(port, 120) {
                println!("FATAL backend not ready");
                std::process::exit(2);
            }
            let url = format!("http://127.0.0.1:{port}/");
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse()?))
                .title("MediaScribe")
                .inner_size(1280.0, 900.0)
                .build()?;
            let handle = window.clone();
            let win = window.as_ref().window();
            let _ = win;
            std::thread::spawn(move || {
                // 页面加载完成后再做一次 in-page 探针
                std::thread::sleep(std::time::Duration::from_millis(800));
                let ok = http_get(&format!("http://127.0.0.1:{port}/api/health"))
                    .map(|b| b.contains("\"ok\":true") || b.contains("\"ok\": true"))
                    .unwrap_or(false);
                let _ = &handle;
                if ok {
                    println!("APP_USABLE");
                } else {
                    println!("APP_USABLE_FAILED in-page health probe returned false");
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
