// Content scripts on youtube.com can't call localhost directly (CORS / private
// network rules), so they ask the service worker to do it.
const SERVER = "http://127.0.0.1:8765";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "getScore") return;

  fetch(`${SERVER}/score?v=${encodeURIComponent(msg.videoId)}`)
    .then((r) => r.json())
    .then(sendResponse)
    .catch(() =>
      sendResponse({
        ok: false,
        error: "server_offline",
        message: "Local server isn't running (python server.py).",
      })
    );
  return true; // keep the channel open for the async response
});
