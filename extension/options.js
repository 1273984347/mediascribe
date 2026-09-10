// MediaScribe Sender — options page script
const $ = (id) => document.getElementById(id);

const DEFAULTS = { endpoint: "http://127.0.0.1:8000", engine: "whisper", model: "small", apiToken: "" };

chrome.storage.sync.get(DEFAULTS, (opts) => {
  $("endpoint").value = opts.endpoint;
  $("engine").value = opts.engine;
  $("model").value = opts.model;
  $("apiToken").value = opts.apiToken || "";
});

$("save").addEventListener("click", () => {
  const opts = {
    endpoint: $("endpoint").value.trim().replace(/\/+$/, ""),
    engine: $("engine").value,
    model: $("model").value,
    apiToken: $("apiToken").value.trim(),
  };
  chrome.storage.sync.set(opts, () => {
    const s = $("status");
    s.textContent = "Saved.";
    s.className = "ok";
    setTimeout(() => { s.textContent = ""; s.className = ""; }, 1500);
  });
});
