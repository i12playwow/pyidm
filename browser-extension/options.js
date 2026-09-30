const $ = (id) => document.getElementById(id);

chrome.storage.local.get("port").then(({ port = 27492 }) => {
  $("port").value = port;
});

$("save").onclick = async () => {
  const port = Number($("port").value) || 27492;
  await chrome.storage.local.set({ port });
  $("msg").textContent = `saved — collector port ${port}`;
  $("msg").className = "";
};

$("test").onclick = async () => {
  const port = Number($("port").value) || 27492;
  try {
    const data = await fetch(`http://127.0.0.1:${port}/ping`).then((r) => r.json());
    $("msg").textContent = `online — PyIDM ${data.version}, saving to ${data.out_dir}`;
    $("msg").className = "";
  } catch {
    $("msg").textContent = "no collector on that port — start it first";
    $("msg").className = "err";
  }
};
