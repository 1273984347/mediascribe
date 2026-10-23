// B2 候选 —— 与 mediascribe/web/static/index.html **等价**的最小工作台壳。
// 元素清单对齐原生页: header(标题 + GPU 指示灯 + 主题切换) + 表单(urls/engine/model/
// language/ocr/cookies/bilingual/api-token/remember-token) + submit + status +
// clear-done + results(复制/下载) + empty-state。
import React, { useCallback, useEffect, useState } from "react";

const ENGINES = [
  ["whisper", "whisper(基线)"],
  ["faster-whisper", "faster-whisper"],
  ["whisperx", "whisperx"],
];

const MODEL_GROUPS = [
  ["快速预览", [["tiny", "tiny"], ["base", "base"], ["small", "small"]]],
  [
    "准确率优先",
    [
      ["medium", "medium"],
      ["large", "large"],
      ["large-v1", "large-v1"],
      ["large-v2", "large-v2"],
      ["large-v3", "large-v3(中文最佳)"],
      ["distil-large-v2", "distil-large-v2"],
      ["distil-large-v3", "distil-large-v3"],
    ],
  ],
];

const OCR = [
  ["auto", "auto"],
  ["paddleocr", "paddleocr"],
  ["pytesseract", "pytesseract"],
  ["easyocr", "easyocr"],
  ["none", "none"],
];

const TOKEN_KEY = "mediascribe.api_token";

export default function App() {
  const [urls, setUrls] = useState("");
  const [engine, setEngine] = useState("whisper");
  const [model, setModel] = useState("small");
  const [language, setLanguage] = useState("");
  const [ocr, setOcr] = useState("auto");
  const [cookies, setCookies] = useState("");
  const [bilingual, setBilingual] = useState(false);
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || "");
  const [remember, setRemember] = useState(() => !!localStorage.getItem(TOKEN_KEY));
  const [status, setStatus] = useState("");
  const [results, setResults] = useState([]);
  const [gpu, setGpu] = useState(null);
  const [theme, setTheme] = useState("light");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const pollHealth = useCallback(async () => {
    try {
      const r = await fetch("/api/health", { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (r.ok) setGpu(await r.json());
    } catch {
      setGpu(null);
    }
  }, [token]);

  useEffect(() => {
    pollHealth();
    const t = setInterval(pollHealth, 5000);
    return () => clearInterval(t);
  }, [pollHealth]);

  const onSubmit = async (e) => {
    e.preventDefault();
    if (remember) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
    setStatus("提交中…");
    try {
      const r = await fetch("/api/jobs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          urls: urls.split(/\s+/).filter(Boolean),
          engine,
          model,
          language: language || undefined,
          ocr,
          cookies: cookies || undefined,
          bilingual,
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data?.detail || `HTTP ${r.status}`);
      setStatus(`已提交: ${data.job_id || data.job_ids?.join(", ") || "ok"}`);
      setResults((prev) => [...prev, data]);
    } catch (err) {
      setStatus(`失败: ${err.message}`);
    }
  };

  const gpuText = gpu
    ? gpu.gpu?.available
      ? `${gpu.gpu.name || gpu.gpu.device} · ${gpu.gpu.vram_used_mb ?? "?"}/${gpu.gpu.vram_total_mb ?? "?"} MB`
      : `CPU (device_hint: ${gpu.device_hint})`
    : "后端未就绪";

  return (
    <div className="wrap">
      <header className="head">
        <h1>MediaScribe</h1>
        <div className="right">
          <span className="pill" id="gpu-pill">
            <span className="dot" id="rec-light" data-on={gpu ? "1" : "0"} />
            <span id="gpu-label">{gpuText}</span>
          </span>
          <button
            type="button"
            className="icon-btn"
            id="theme-toggle"
            title="切换主题"
            onClick={() => setTheme((t) => (t === "light" ? "dark" : "light"))}
          >
            {theme === "light" ? "🌙" : "☀️"}
          </button>
        </div>
      </header>

      <main>
        <form id="form" onSubmit={onSubmit}>
          <div className="fld">
            <label htmlFor="urls">
              视频链接 <span className="hint">每行一个</span>
            </label>
            <textarea
              id="urls"
              rows={4}
              value={urls}
              onChange={(e) => setUrls(e.target.value)}
              placeholder="https://..."
            />
          </div>

          <div className="row">
            <div className="fld">
              <label htmlFor="engine">
                识别引擎 <span className="hint">可选</span>
              </label>
              <select id="engine" value={engine} onChange={(e) => setEngine(e.target.value)}>
                {ENGINES.map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </div>

            <div className="fld">
              <label htmlFor="model">
                模型 <span className="hint">默认 small</span>
              </label>
              <select id="model" value={model} onChange={(e) => setModel(e.target.value)}>
                {MODEL_GROUPS.map(([group, items]) => (
                  <optgroup key={group} label={group}>
                    {items.map(([v, l]) => (
                      <option key={v} value={v}>{l}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>

            <div className="fld">
              <label htmlFor="language">
                语言 <span className="hint">可留空</span>
              </label>
              <input
                type="text"
                id="language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                placeholder="zh, en, ja, …"
              />
            </div>
          </div>

          <div className="row">
            <div className="fld">
              <label htmlFor="ocr">
                图文识别 <span className="hint">公众号图片</span>
              </label>
              <select id="ocr" value={ocr} onChange={(e) => setOcr(e.target.value)}>
                {OCR.map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </div>

            <div className="fld">
              <label htmlFor="cookies">
                公众号 Cookies <span className="hint">可选</span>
              </label>
              <input
                type="text"
                id="cookies"
                value={cookies}
                onChange={(e) => setCookies(e.target.value)}
              />
            </div>
          </div>

          <div className="row">
            <label className="inline">
              <input
                type="checkbox"
                id="bilingual"
                checked={bilingual}
                onChange={(e) => setBilingual(e.target.checked)}
              />
              输出选项: 双语对照
            </label>
          </div>

          <div className="row">
            <div className="fld grow">
              <label htmlFor="api-token">
                API 令牌 <span className="hint">无鉴权时可留空</span>
              </label>
              <input
                type="password"
                id="api-token"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </div>
            <label className="inline">
              <input
                type="checkbox"
                id="remember-token"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              记住令牌
            </label>
          </div>

          <div className="actions">
            <button className="run" id="submit" type="submit">开始转写</button>
            <button
              className="ghost"
              id="clear-done"
              type="button"
              onClick={() => setResults([])}
            >
              清空结果
            </button>
            <span id="status" className="status">{status}</span>
          </div>
        </form>

        <section id="results" className="results">
          {results.length === 0 ? (
            <div id="empty-state" className="empty">还没有任务结果</div>
          ) : (
            results.map((r, i) => (
              <article key={i} className="card">
                <pre>{JSON.stringify(r, null, 2)}</pre>
                <div className="row">
                  <button
                    type="button"
                    data-copy
                    onClick={() => navigator.clipboard?.writeText(JSON.stringify(r, null, 2))}
                  >
                    复制
                  </button>
                  <button
                    type="button"
                    data-download
                    onClick={() => {
                      const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
                      const a = document.createElement("a");
                      a.href = URL.createObjectURL(blob);
                      a.download = `mediascribe-${i}.json`;
                      a.click();
                    }}
                  >
                    下载
                  </button>
                </div>
              </article>
            ))
          )}
        </section>
      </main>
    </div>
  );
}
