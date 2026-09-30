// PyIDM Collector — background service worker (MV3).
// Owns the context menu ("Send link to PyIDM…"), the collector URL, and the
// POST helper every surface uses. The popup talks to the same endpoints.

const DEFAULT_PORT = 27492;

async function baseUrl() {
  const { port = DEFAULT_PORT } = await chrome.storage.local.get("port");
  return `http://127.0.0.1:${port}`;
}

async function post(path, body) {
  const res = await fetch(`${await baseUrl()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function notify(message) {
  try {
    await chrome.notifications.create({
      type: "basic", iconUrl: "icon.png", title: "PyIDM", message,
    });
  } catch { /* notifications may be unavailable; the popup still shows it */ }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "pyidm-send-link",
    title: "Send link to PyIDM…",
    contexts: ["link"],
  });
  chrome.contextMenus.create({
    id: "pyidm-send-page",
    title: "Queue this page URL",
    contexts: ["page"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  try {
    const url = info.menuItemId === "pyidm-send-page" ? tab.url : info.linkUrl;
    const data = await post("/collect", { urls: [url], page_url: tab?.url || "" });
    notify(data.added > 0
      ? `Queued ${data.added} link(s) — ${data.pending} pending in PyIDM`
      : "Already in the PyIDM queue");
  } catch (e) {
    notify(`PyIDM collector unreachable (${e.message}). Start it from the GUI Tools menu or 'idm collect'.`);
  }
});

// The popup asks for a page scan; do the scripting here (the popup can die
// mid-scan, the service worker shouldn't depend on it).
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "scan") return false;
  (async () => {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: msg.tabId },
        func: () => {
          const pageUrl = location.href;
          const links = [...document.querySelectorAll("a[href]")]
            .map((a) => a.href)
            .filter((h) => /^https?:/i.test(h));
          const media = performance.getEntriesByType("resource")
            .map((r) => r.name)
            .concat([...document.querySelectorAll("video, audio, source, img[src]")]
              .map((m) => m.src || m.currentSrc || ""))
            .filter((h) => /^https?:/i.test(h));
          return { pageUrl, links: [...new Set(links)], media: [...new Set(media)] };
        },
      });
      const r = results?.[0]?.result;
      if (!r) throw new Error("scan returned nothing (restricted page?)");
      sendResponse({ ok: true, ...r });
    } catch (e) {
      sendResponse({ ok: false, error: String(e.message || e) });
    }
  })();
  return true;   // async sendResponse
});
