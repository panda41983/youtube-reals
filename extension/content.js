// Floating badge that shows the AI-generated probability for the current video.
// YouTube is a single-page app, so we re-check on every in-app navigation.

const BADGE_ID = "ytai-badge";
let currentVideoId = null;

function getVideoId() {
  const url = new URL(location.href);
  if (url.pathname === "/watch") return url.searchParams.get("v");
  const shorts = url.pathname.match(/^\/shorts\/([A-Za-z0-9_-]{11})/);
  return shorts ? shorts[1] : null;
}

function getBadge() {
  let badge = document.getElementById(BADGE_ID);
  if (!badge) {
    badge = document.createElement("div");
    badge.id = BADGE_ID;
    badge.innerHTML = `
      <button class="ytai-close" title="Hide">×</button>
      <div class="ytai-label">AI-generated</div>
      <div class="ytai-body"></div>`;
    badge.querySelector(".ytai-close").addEventListener("click", () => {
      badge.hidden = true;
    });
    document.body.appendChild(badge);
  }
  badge.hidden = false;
  return badge;
}

function removeBadge() {
  document.getElementById(BADGE_ID)?.remove();
}

// 0 → green, 0.5 → amber, 1 → red.
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

function check() {
  const videoId = getVideoId();
  if (videoId === currentVideoId) return;
  currentVideoId = videoId;

  if (!videoId) return removeBadge();

  showLoading();
  chrome.runtime.sendMessage({ type: "getScore", videoId }, (res) => {
    if (videoId !== currentVideoId) return; // user moved on to another video
    if (chrome.runtime.lastError || !res) return showError("Extension error.");
    if (res.ok) showScore(res.score);
    else showError(res.message || res.error);
  });
}

document.addEventListener("yt-navigate-finish", check);
window.addEventListener("popstate", check);
check();
