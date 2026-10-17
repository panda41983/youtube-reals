// Content scripts on youtube.com can't call localhost directly (CORS / private
// network rules), so they ask the service worker to do it.
const SERVER = "http://127.0.0.1:8765";
const NOTES_KEY = "notesByVideo";

function storageGet(keys) {
  return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
}

function storageSet(obj) {
  return new Promise((resolve) => chrome.storage.local.set(obj, resolve));
}

async function listNotes() {
  const data = await storageGet([NOTES_KEY]);
  const map = data[NOTES_KEY] || {};
  return Object.values(map).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
}

async function saveNote(entry) {
  const data = await storageGet([NOTES_KEY]);
  const map = data[NOTES_KEY] || {};
  map[entry.videoId] = entry;
  await storageSet({ [NOTES_KEY]: map });
  return entry;
}

async function deleteNote(videoId) {
  const data = await storageGet([NOTES_KEY]);
  const map = data[NOTES_KEY] || {};
  delete map[videoId];
  await storageSet({ [NOTES_KEY]: map });
  return { ok: true };
}

async function getSavedNote(videoId) {
  const data = await storageGet([NOTES_KEY]);
  return (data[NOTES_KEY] || {})[videoId] || null;
}

function fetchJson(path) {
  return fetch(`${SERVER}${path}`)
    .then((r) => r.json())
    .catch(() => ({
      ok: false,
      error: "server_offline",
      message: "Local server isn't running (python server.py).",
    }));
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg?.type) return;

  if (msg.type === "getScore") {
    fetchJson(`/score?v=${encodeURIComponent(msg.videoId)}`).then(sendResponse);
    return true;
  }

  if (msg.type === "getNotes") {
    const lang = msg.lang || "es";
    fetchJson(
      `/notes?v=${encodeURIComponent(msg.videoId)}&lang=${encodeURIComponent(lang)}`
    ).then(async (res) => {
      if (res?.ok) {
        const entry = {
          videoId: msg.videoId,
          title: msg.title || msg.videoId,
          lang: res.lang || lang,
          notes: res.notes || "",
          translation: res.translation || "",
          raw: res.raw || "",
          savedAt: Date.now(),
          url: msg.url || `https://www.youtube.com/watch?v=${msg.videoId}`,
        };
        await saveNote(entry);
        sendResponse({ ok: true, note: entry });
      } else {
        sendResponse(res);
      }
    });
    return true;
  }

  if (msg.type === "listNotes") {
    listNotes().then((notes) => sendResponse({ ok: true, notes }));
    return true;
  }

  if (msg.type === "getSavedNote") {
    getSavedNote(msg.videoId).then((note) => sendResponse({ ok: true, note }));
    return true;
  }

  if (msg.type === "deleteNote") {
    deleteNote(msg.videoId).then(sendResponse);
    return true;
  }
});
