// MediaScribe Sender — background service worker
//
// Responsibilities:
//   1. Configure the Side Panel so the same ``popup.html`` is shared
//      between the toolbar action and the browser's right-side
//      panel.  Without this call, ``sidePanel.open`` would default to
//      the extension's index page.
//   2. Implement the toolbar-click fallback for users who have
//      disabled the popup (chrome.action.default_popup can be
//      overridden by ``disable_popup`` in the manifest, or by the
//      user via chrome://extensions).  In that case we open the
//      side panel on click.
//   3. Open the options page on first install so the user can set
//      their endpoint / token.

const V2T_VERSION = "3.1.0";

function configureSidePanel() {
  if (!chrome.sidePanel || !chrome.sidePanel.setPanelBehavior) return;
  // The same popup.html works for both surfaces — ``openSidePanel``
  // detects which surface it is on via the ``chrome.sidePanel``
  // presence check inside popup.js.
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: false })
    .catch((err) => console.warn("[V2T] setPanelBehavior failed:", err));
}

chrome.runtime.onInstalled.addListener((details) => {
  configureSidePanel();
  if (details.reason === "install") {
    // First-run hint: open the options page so the user can set the
    // endpoint if the default (http://127.0.0.1:8000) doesn't match.
    chrome.runtime.openOptionsPage();
  }
});

chrome.runtime.onStartup.addListener(() => {
  configureSidePanel();
});

// Action click fallback.  If the user has uninstalled the popup
// (e.g. via chrome://extensions) we still want a click on the
// toolbar icon to do *something* useful, so we open the side panel
// for the current tab.
chrome.action.onClicked.addListener((tab) => {
  if (!chrome.sidePanel || !chrome.sidePanel.open) {
    // Pre-114 fallback: just open the options page.
    chrome.runtime.openOptionsPage();
    return;
  }
  const opts = tab && tab.id ? { tabId: tab.id } : {};
  chrome.sidePanel.open(opts).catch((err) => {
    console.warn("[V2T] sidePanel.open failed:", err);
  });
});

// Cross-tab message bus: when the user clicks the "Open in side
// panel" button in the popup, the popup routes the request through
// the background worker (popups do not have direct ``sidePanel.open``
// access in all browser versions).
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "v2t/openSidePanel" && msg.tabId != null) {
    if (chrome.sidePanel && chrome.sidePanel.open) {
      chrome.sidePanel
        .open({ tabId: msg.tabId })
        .then(() => sendResponse({ ok: true }))
        .catch((err) => sendResponse({ ok: false, error: String(err) }));
      return true; // async response
    }
    sendResponse({ ok: false, error: "sidePanel API not available" });
    return false;
  }
  return false;
});

// Exposed for debug / test tooling.
self.__v2t_version = V2T_VERSION;
