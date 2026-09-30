// PyIDM Collector — popup logic.
const DEFAULT_PORT = 27492;
let scan = null;          // { pageUrl, links: [], media: [] }
let selected = new Set();

const $ = (id) => document.getElementById(id);
const status = (msg, err = false) => {
  $("status").textContent = msg;
  $("status").className = err ? "err" : "";
};

async function baseUrl() {
  const { port = DEFAULT_PORT } = await chrome.storage.local.get("port");
  return `http://127.0.0.1:${port}`;
}

async function ping() {
  try {
    const res = await fetch(`${await baseUrl()}/ping`);
    const data = await res.json();
    status(`collector online — PyIDM ${data.version}, saving to ${data.out_dir}`);
    return true;
  } catch {
    status("collector offline — start it from the PyIDM GUI Tools menu or run 'idm collect'", true);
    return false;
  }
}

function renderList() {
  const urls = $("media").dataset.active === "1"
    ? [...new Set([...scan.media, ...scan.links])]   // media first, then links
    : scan.links;
  $("listbox").hidden = false;
  $("list").innerHTML = "";
  for (const u of urls) {
    const li = document.createElement("li");
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = selected.has(u);
    cb.onchange = () => (cb.checked ? selected.add(u) : selected.delete(u));
    const text = document.createElement("span");
    text.textContent = u;
    label.append(cb, " ", text);
    li.append(label);
    $("list").append(li);
  }
  $("send").textContent = `Send ${selected.size || urls.length} to PyIDM`;
}

async function doScan(mediaOnly) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  status("scanning page…");
  const r = await chrome.runtime.sendMessage({ type: "scan", tabId: tab.id });
  if (!r?.ok) return status(r?.error || "scan failed", true);
  scan = { pageUrl: r.pageUrl, links: r.links, media: r.media };
  $("media").dataset.active = mediaOnly ? "1" : "0";
  selected = new Set(mediaOnly ? scan.media : scan.links);
  if (mediaOnly && !scan.media.length) return status("no media found on this page", true);
  status(`found ${scan.links.length} link(s), ${scan.media.length} media file(s)`);
  renderList();
}

async function send() {
  if (!scan) return status("scan a page first", true);
  const urls = [...selected];
  if (!urls.length) return status("nothing checked", true);
  const path = $("start").checked ? "/download" : "/collect";
  try {
    const data = await fetch(`${await baseUrl()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls, page_url: scan.pageUrl }),
    }).then((r) => r.json());
    if (data.ok === false) throw new Error(data.error || "rejected");
    status($("start").checked
      ? `started ${data.started} download(s) — ${data.summary?.ok ?? "?"} finished`
      : `queued ${data.added} — ${data.pending} pending in PyIDM`);
  } catch (e) {
    status(`send failed: ${e.message}`, true);
  }
}

async function clearQueue() {
  try {
    const data = await fetch(`${await baseUrl()}/queue/clear`, { method: "POST" })
      .then((r) => r.json());
    status(`queue cleared (${data.cleared} pending dropped)`);
  } catch (e) {
    status(`clear failed: ${e.message}`, true);
  }
}

$("scan").onclick = () => doScan(false);
$("media").onclick = () => doScan(true);
$("page").onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const data = await fetch(`${await baseUrl()}/collect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ urls: [tab.url], page_url: tab.url }),
  }).then((r) => r.json()).catch(() => null);
  status(data?.ok ? `queued the page URL (${data.pending} pending)`
                  : "collector offline — start it first", !data?.ok);
};
$("send").onclick = send;
$("clear").onclick = clearQueue;
ping();
