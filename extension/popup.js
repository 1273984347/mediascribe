// Video2Text Sender — popup / side panel script
//
// Same code path is used for both the toolbar popup (chrome.action
// default_popup) and the Chrome Side Panel (chrome.sidePanel).  We
// detect which surface we are on and adjust a few affordances:
//   * the side panel is wider and tall, so the result <pre> can be
//     bigger;
//   * the "Open in side panel" button is only shown in the popup;
//   * the layout is otherwise identical so styles can be shared via
//     popup.css.

const $ = (id) => document.getElementById(id);

const DEFAULTS = {
  endpoint: "http://127.0.0.1:8000",
  engine: "whisper",
  model: "small",
  apiToken: "",
};

function loadOptions() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(DEFAULTS, (opts) => resolve(opts));
  });
}

function saveOptions(opts) {
  return new Promise((resolve) => {
    chrome.storage.sync.set(opts, () => resolve());
  });
}

function getActiveUrl() {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs && tabs[0];
      resolve(tab ? tab.url : null);
    });
  });
}

function detectSurface() {
  // The popup window is opened by chrome.action.default_popup — its
  // document has no chrome.sidePanel context.  The side panel, on the
  // other hand, is rendered inside the browser's panel frame and
  // exposes window.chrome.sidePanel as a non-null object.
  const isSidePanel = !!(chrome.sidePanel && chrome.sidePanel.open);
  if (isSidePanel) {
    document.body.classList.add("is-side-panel");
  }
  return isSidePanel;
}

function openSidePanel() {
  // The runtime API exposes ``open`` since Chrome 114 / Edge 114.  If
  // the user is on an older browser the call returns undefined and we
  // surface a hint instead of throwing.
  if (!chrome.sidePanel || !chrome.sidePanel.open) {
    $("status").textContent =
      "Side panel requires Chrome / Edge 114+. Update your browser.";
    $("status").className = "err";
    return;
  }
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0];
    if (!tab) return;
    // Some browsers (notably older Chromium MV3 builds) expose the
    // sidePanel API on the *background* worker only, not on the
    // popup.  We try the local call first and fall back to a
    // message-bus call routed through the background worker.
    const tryBackground = () => {
      chrome.runtime.sendMessage(
        { type: "v2t/openSidePanel", tabId: tab.id },
        (resp) => {
          if (chrome.runtime.lastError || !resp || !resp.ok) {
            $("status").textContent =
              "Cannot open side panel: " +
              (resp ? resp.error : (chrome.runtime.lastError && chrome.runtime.lastError.message) || "unknown");
            $("status").className = "err";
          }
        }
      );
    };
    try {
      const maybe = chrome.sidePanel.open({ tabId: tab.id });
      if (maybe && typeof maybe.then === "function") {
        maybe.catch(() => tryBackground());
      }
    } catch (_e) {
      tryBackground();
    }
    // The popup will close on its own after the click, so no need to
    // hide any UI explicitly.
  });
}

async function init() {
  detectSurface();
  const opts = await loadOptions();
  $("endpoint").value = opts.endpoint || "http://127.0.0.1:8000";
  $("engine").value = opts.engine || "whisper";
  $("model").value = opts.model || "small";
  $("apiToken").value = opts.apiToken || "";
  const url = await getActiveUrl();
  $("url").textContent = url || "(no active tab)";
  $("send").disabled = !url;

  // Wire up side-only buttons.
  const openOptions = $("openOptions");
  if (openOptions) {
    openOptions.addEventListener("click", () => {
      chrome.runtime.openOptionsPage();
    });
  }
  const openSide = $("openSidePanel");
  if (openSide) {
    openSide.addEventListener("click", openSidePanel);
  }
}

async function send() {
  // Read user-entered form values for this request.
  const endpoint = $("endpoint").value.trim().replace(/\/+$/, "");
  const engine = $("engine").value;
  const model = $("model").value;
  const url = $("url").textContent.trim();
  if (!url || url === "(no active tab)") return;

  // Auth token: form field first, then storage as fallback.
  // The token is never exposed in storage unless the user sets it
  // via the options page; the popup form is the primary UX path.
  const opts = await loadOptions();
  const apiToken = $("apiToken").value.trim() || opts.apiToken || "";
  const headers = { "Content-Type": "application/json" };
  if (apiToken) {
    headers["Authorization"] = "Bearer " + apiToken;
  }

  $("send").disabled = true;
  $("status").textContent = "Sending...";
  $("status").className = "hint";
  try {
    const resp = await fetch(endpoint + "/api/transcribe", {
      method: "POST",
      headers,
      body: JSON.stringify({ urls: [url], engine, model }),
    });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(detail.detail || resp.statusText);
    }
    const data = await resp.json();
    const item = (data.results || [])[0];
    if (!item) throw new Error("Empty response from server");
    if (!item.ok) throw new Error(item.error || "Transcription failed");
    $("status").textContent = "Done.";
    $("status").className = "ok";
    $("result").style.display = "block";
    $("result").textContent = item.markdown || "";
  } catch (err) {
    $("status").textContent = "Error: " + err.message;
    $("status").className = "err";
  } finally {
    $("send").disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("send").addEventListener("click", send);
  init();
});
