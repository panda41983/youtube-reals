// Floating badge: AI score + Notes (Slack/Grammarly study notes + translation).
// YouTube is a single-page app, so we re-check on every in-app navigation.

const BADGE_ID = "ytai-badge";
const PANEL_ID = "ytai-notes-panel";
const LANGS = [
  { code: "es", label: "Spanish" },
  { code: "en", label: "English" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "pt", label: "Portuguese" },
  { code: "hi", label: "Hindi" },
  { code: "zh", label: "Chinese" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
];

let currentVideoId = null;
let panelMode = "current"; // current | library

function getVideoId() {
  const url = new URL(location.href);
  if (url.pathname === "/watch") return url.searchParams.get("v");
  const shorts = url.pathname.match(/^\/shorts\/([A-Za-z0-9_-]{11})/);
  return shorts ? shorts[1] : null;
}

function getVideoTitle() {
  const el =
    document.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
    document.querySelector("h1 yt-formatted-string") ||
    document.querySelector("h2.ytd-reel-player-header-renderer yt-formatted-string");
  const t = (el?.textContent || "").trim();
  if (t) return t;
  return document.title.replace(/\s*-\s*YouTube\s*$/, "").trim() || "YouTube video";
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatMarkdownLite(text) {
  const lines = String(text || "").split("\n");
  const out = [];
  for (const line of lines) {
    if (/^###\s+/.test(line)) {
      out.push(`<h4>${escapeHtml(line.replace(/^###\s+/, ""))}</h4>`);
    } else if (/^##\s+/.test(line)) {
      out.push(`<h3>${escapeHtml(line.replace(/^##\s+/, ""))}</h3>`);
    } else if (/^#\s+/.test(line)) {
      out.push(`<h3>${escapeHtml(line.replace(/^#\s+/, ""))}</h3>`);
    } else if (/^[-*]\s+/.test(line)) {
      out.push(`<div class="ytai-li">• ${escapeHtml(line.replace(/^[-*]\s+/, ""))}</div>`);
    } else if (!line.trim()) {
      out.push("<br>");
    } else {
      out.push(`<p>${escapeHtml(line)}</p>`);
    }
  }
  return out.join("");
}

function getBadge() {
  let badge = document.getElementById(BADGE_ID);
  if (!badge) {
    badge = document.createElement("div");
    badge.id = BADGE_ID;
    badge.innerHTML = `
      <button class="ytai-close" title="Hide">×</button>
      <div class="ytai-label">AI-generated</div>
      <div class="ytai-body"></div>
      <button type="button" class="ytai-notes-btn">Notes</button>`;
    badge.querySelector(".ytai-close").addEventListener("click", () => {
      badge.hidden = true;
      closePanel();
    });
    badge.querySelector(".ytai-notes-btn").addEventListener("click", () => {
      togglePanel();
    });
    document.body.appendChild(badge);
  }
  badge.hidden = false;
  return badge;
}

function removeBadge() {
  document.getElementById(BADGE_ID)?.remove();
  closePanel();
}

function scoreColor(p) {
  return `hsl(${Math.round(120 * (1 - p))}, 75%, 45%)`;
}

function verdict(p) {
  if (p < 0.33) return "Likely human";
  if (p < 0.66) return "Uncertain";
  return "Likely AI";
}

function showLoading() {
  const badge = getBadge();
  badge.style.setProperty("--ytai-color", "#888");
  badge.querySelector(".ytai-body").innerHTML = `
    <div class="ytai-spinner"></div>
    <div class="ytai-note">Analyzing transcript…</div>`;
}

function showScore(p) {
  const badge = getBadge();
  badge.style.setProperty("--ytai-color", scoreColor(p));
  badge.querySelector(".ytai-body").innerHTML = `
    <div class="ytai-score">${Math.round(p * 100)}%</div>
    <div class="ytai-bar"><div style="width:${p * 100}%"></div></div>
    <div class="ytai-note">${verdict(p)}</div>`;
}

function showError(message) {
  const badge = getBadge();
  badge.style.setProperty("--ytai-color", "#888");
  const body = badge.querySelector(".ytai-body");
  body.innerHTML = `<div class="ytai-score ytai-na">—</div><div class="ytai-note"></div>`;
  body.querySelector(".ytai-note").textContent = message;
}

function closePanel() {
  document.getElementById(PANEL_ID)?.remove();
}

function getPanel() {
  let panel = document.getElementById(PANEL_ID);
  if (!panel) {
    panel = document.createElement("div");
    panel.id = PANEL_ID;
    document.body.appendChild(panel);
  }
  return panel;
}

function togglePanel() {
  const existing = document.getElementById(PANEL_ID);
  if (existing) {
    closePanel();
    return;
  }
  panelMode = "current";
  openCurrentNotes();
}

function langOptionsHtml(selected) {
  return LANGS.map(
    (l) =>
      `<option value="${l.code}" ${l.code === selected ? "selected" : ""}>${l.label}</option>`
  ).join("");
}

function openCurrentNotes() {
  const videoId = currentVideoId || getVideoId();
  if (!videoId) return;
  const panel = getPanel();
  panel.innerHTML = `
    <div class="ytai-panel-head">
      <div class="ytai-panel-title">Notes</div>
      <div class="ytai-panel-actions">
        <button type="button" class="ytai-linkbtn" data-action="library">Saved</button>
        <button type="button" class="ytai-close" title="Close">×</button>
      </div>
    </div>
    <div class="ytai-panel-sub" id="ytai-panel-title"></div>
    <div class="ytai-controls">
      <label>
        Translation
        <select id="ytai-lang">${langOptionsHtml("es")}</select>
      </label>
      <button type="button" class="ytai-primary" data-action="generate">Generate notes</button>
    </div>
    <div class="ytai-panel-body" id="ytai-panel-body">
      <div class="ytai-muted">Ask the Slack agent for event details, main points, tips, and a translation.</div>
    </div>`;

  panel.querySelector(".ytai-close").addEventListener("click", closePanel);
  panel.querySelector('[data-action="library"]').addEventListener("click", openLibrary);
  panel.querySelector('[data-action="generate"]').addEventListener("click", () => {
    generateNotes(videoId);
  });
  panel.querySelector("#ytai-panel-title").textContent = getVideoTitle();

  chrome.runtime.sendMessage({ type: "getSavedNote", videoId }, (res) => {
    if (chrome.runtime.lastError || !res?.note) return;
    const langSel = panel.querySelector("#ytai-lang");
    if (langSel && res.note.lang) langSel.value = res.note.lang;
    renderNoteBody(res.note);
  });
}

function renderNoteBody(note) {
  const body = document.getElementById("ytai-panel-body");
  if (!body || !note) return;
  const when = note.savedAt ? new Date(note.savedAt).toLocaleString() : "";
  body.innerHTML = `
    <div class="ytai-meta">Saved ${escapeHtml(when)} · lang ${escapeHtml(note.lang || "")}</div>
    <section class="ytai-section">
      <h3>Study notes</h3>
      <div class="ytai-md">${formatMarkdownLite(note.notes)}</div>
    </section>
    <section class="ytai-section">
      <h3>Translation</h3>
      <div class="ytai-md">${
        note.translation
          ? formatMarkdownLite(note.translation)
          : '<div class="ytai-muted">No translation section in the agent reply yet.</div>'
      }</div>
    </section>
    <div class="ytai-footer-actions">
      <button type="button" class="ytai-danger" data-action="delete">Delete note</button>
    </div>`;
  body.querySelector('[data-action="delete"]')?.addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "deleteNote", videoId: note.videoId }, () => {
      body.innerHTML = `<div class="ytai-muted">Deleted. Generate again anytime.</div>`;
    });
  });
}

function generateNotes(videoId) {
  const panel = document.getElementById(PANEL_ID);
  if (!panel) return;
  const lang = panel.querySelector("#ytai-lang")?.value || "es";
  const body = panel.querySelector("#ytai-panel-body");
  const btn = panel.querySelector('[data-action="generate"]');
  if (btn) btn.disabled = true;
  body.innerHTML = `
    <div class="ytai-spinner"></div>
    <div class="ytai-muted">Sending transcript to Slack… waiting for notes + translation.</div>`;

  chrome.runtime.sendMessage(
    {
      type: "getNotes",
      videoId,
      lang,
      title: getVideoTitle(),
      url: location.href,
    },
    (res) => {
      if (btn) btn.disabled = false;
      if (videoId !== currentVideoId && videoId !== getVideoId()) return;
      if (chrome.runtime.lastError || !res) {
        body.innerHTML = `<div class="ytai-error">Extension error.</div>`;
        return;
      }
      if (!res.ok) {
        body.innerHTML = `<div class="ytai-error">${escapeHtml(res.message || res.error || "Failed")}</div>`;
        return;
      }
      renderNoteBody(res.note);
    }
  );
}

function openLibrary() {
  panelMode = "library";
  const panel = getPanel();
  panel.innerHTML = `
    <div class="ytai-panel-head">
      <div class="ytai-panel-title">Saved notes</div>
      <div class="ytai-panel-actions">
        <button type="button" class="ytai-linkbtn" data-action="back">Back</button>
        <button type="button" class="ytai-close" title="Close">×</button>
      </div>
    </div>
    <div class="ytai-panel-body" id="ytai-panel-body">
      <div class="ytai-muted">Loading…</div>
    </div>`;
  panel.querySelector(".ytai-close").addEventListener("click", closePanel);
  panel.querySelector('[data-action="back"]').addEventListener("click", () => {
    panelMode = "current";
    openCurrentNotes();
  });

  chrome.runtime.sendMessage({ type: "listNotes" }, (res) => {
    const body = panel.querySelector("#ytai-panel-body");
    if (!body) return;
    if (chrome.runtime.lastError || !res?.ok) {
      body.innerHTML = `<div class="ytai-error">Could not load notes.</div>`;
      return;
    }
    const notes = res.notes || [];
    if (!notes.length) {
      body.innerHTML = `<div class="ytai-muted">No saved notes yet.</div>`;
      return;
    }
    body.innerHTML = `<div class="ytai-list">${notes
      .map(
        (n) => `
      <button type="button" class="ytai-list-item" data-id="${escapeHtml(n.videoId)}">
        <div class="ytai-list-title">${escapeHtml(n.title || n.videoId)}</div>
        <div class="ytai-list-meta">${escapeHtml(n.lang || "")} · ${escapeHtml(
          n.savedAt ? new Date(n.savedAt).toLocaleDateString() : ""
        )}</div>
      </button>`
      )
      .join("")}</div>`;
    body.querySelectorAll(".ytai-list-item").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-id");
        const note = notes.find((n) => n.videoId === id);
        if (!note) return;
        panelMode = "current";
        openCurrentNotes();
        // After panel rebuild, paint this note (may be for another video).
        setTimeout(() => renderNoteBody(note), 0);
      });
    });
  });
}

function check() {
  const videoId = getVideoId();
  if (videoId === currentVideoId) return;
  currentVideoId = videoId;

  if (!videoId) return removeBadge();

  closePanel();
  showLoading();
  chrome.runtime.sendMessage({ type: "getScore", videoId }, (res) => {
    if (videoId !== currentVideoId) return;
    if (chrome.runtime.lastError || !res) return showError("Extension error.");
    if (res.ok) showScore(res.score);
    else showError(res.message || res.error);
  });
}

document.addEventListener("yt-navigate-finish", check);
window.addEventListener("popstate", check);
check();
