"""Local bridge between the Chrome extension and the Slack agent.

GET /score?v=<videoId>
  1. Fetches the video's transcript with youtube-transcript-api.
  2. Uploads it as <videoId>.txt to #new-channel with the message "<videoId>".
  3. Polls #output-port for a reply of the form "<videoId>: 0.86".
  4. Returns {"ok": true, "score": 0.86} or {"ok": false, "error": ..., "message": ...}.

GET /notes?v=<videoId>&lang=<code>
  1. Fetches the transcript.
  2. Uploads it with a NOTES request (study notes + translation).
  3. Polls #output-port for "NOTES <videoId>" + markdown body.
  4. Returns {"ok": true, "notes": "...", "translation": "...", "lang": "..."}.
"""

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from youtube_transcript_api import CouldNotRetrieveTranscript, YouTubeTranscriptApi

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
INPUT_CHANNEL = os.environ.get("INPUT_CHANNEL_ID", "C0C9TSADLSZ")  # #new-channel
OUTPUT_CHANNEL = os.environ.get("OUTPUT_CHANNEL_ID", "C0CA2SEFMNY")  # #output-port
PORT = int(os.environ.get("PORT", "8765"))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "1.5"))
TIMEOUT = float(os.environ.get("TIMEOUT", "60"))
NOTES_TIMEOUT = float(os.environ.get("NOTES_TIMEOUT", "120"))
NOTES_DEFAULT_LANG = os.environ.get("NOTES_DEFAULT_LANG", "es")
# How far back to scan #output-port for an already-posted reply.
LOOKUP_LIMIT = int(os.environ.get("LOOKUP_LIMIT", "200"))

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
LANG_RE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]+)?$")
TRANSLATION_SPLIT = re.compile(r"^##\s*Translation\s*$", re.IGNORECASE | re.MULTILINE)

slack = WebClient(token=SLACK_BOT_TOKEN.strip())
ytt = YouTubeTranscriptApi()

# Scores already received, so revisiting a video doesn't re-query the agent.
_cache: dict[str, float] = {}
# Notes cache keyed by "videoId:lang".
_notes_cache: dict[str, dict] = {}
# One lock per key so duplicate requests share one Slack round trip.
_video_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class ScoreError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def slack_error_text(e: SlackApiError) -> str:
    err = e.response.get("error", "unknown_error")
    needed = e.response.get("needed")
    if needed:
        return f"{err} (needs {needed})"
    if err == "not_in_channel":
        return f"{err} (invite the bot to the channel with /invite)"
    return err


def lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _video_locks.setdefault(key, threading.Lock())


def fetch_transcript(video_id: str) -> str:
    try:
        try:
            fetched = ytt.fetch(video_id, languages=["en", "en-US", "en-GB"])
        except CouldNotRetrieveTranscript:
            # No English track: fall back to whatever transcript exists.
            fetched = next(iter(ytt.list(video_id))).fetch()
    except StopIteration:
        raise ScoreError("no_transcript", "This video has no transcript.")
    except CouldNotRetrieveTranscript as e:
        raise ScoreError("no_transcript", type(e).__name__)
    text = "\n".join(s.text for s in fetched.snippets).strip()
    if not text:
        raise ScoreError("no_transcript", "Transcript is empty.")
    return text


def post_transcript(video_id: str, transcript: str) -> None:
    try:
        slack.files_upload_v2(
            channel=INPUT_CHANNEL,
            content=transcript,
            filename=f"{video_id}.txt",
            title=f"{video_id}.txt",
            initial_comment=video_id,
            request_file_info=False,  # skips files.info, which would need files:read
        )
    except SlackApiError as e:
        raise ScoreError("slack_error", f"Upload failed: {slack_error_text(e)}")


def notes_request_comment(video_id: str, lang: str) -> str:
    return (
        f"NOTES {video_id} lang={lang}\n"
        f"\n"
        f"Read the attached transcript and reply in #output-port with ONE message that starts with:\n"
        f"NOTES {video_id}\n"
        f"\n"
        f"Then include this markdown (fill every section; use 'None noted' if empty):\n"
        f"## Event details\n"
        f"- dates, times, places, deadlines, RSVP/links if mentioned\n"
        f"## Main points\n"
        f"- core topic ideas, math/concepts, arguments\n"
        f"## Tips\n"
        f"- big practical tips or takeaways\n"
        f"## Translation\n"
        f"- translate the core content into language code '{lang}' "
        f"(concise but complete; keep names/formulas readable)\n"
    )


def post_notes_request(video_id: str, transcript: str, lang: str) -> None:
    try:
        slack.files_upload_v2(
            channel=INPUT_CHANNEL,
            content=transcript,
            filename=f"{video_id}-notes.txt",
            title=f"{video_id} notes request",
            initial_comment=notes_request_comment(video_id, lang),
            request_file_info=False,
        )
    except SlackApiError as e:
        raise ScoreError("slack_error", f"Upload failed: {slack_error_text(e)}")


def score_pattern(video_id: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(video_id)}\s*:\s*(\d*\.?\d+)")


def parse_score_from_messages(video_id: str, messages: list) -> float | None:
    pattern = score_pattern(video_id)
    # Messages come newest first; take the newest matching reply.
    for msg in messages:
        text = msg.get("text", "")
        # Ignore NOTES replies when looking for a score.
        if re.match(rf"^\s*NOTES\s+{re.escape(video_id)}\b", text, re.IGNORECASE):
            continue
        m = pattern.search(text)
        if m:
            score = float(m.group(1))
            if 0.0 <= score <= 1.0:
                return score
    return None


def channel_history(*, oldest: float | None = None) -> list:
    kwargs = {"channel": OUTPUT_CHANNEL, "limit": LOOKUP_LIMIT}
    if oldest is not None:
        kwargs["oldest"] = f"{oldest:.6f}"
    try:
        resp = slack.conversations_history(**kwargs)
    except SlackApiError as e:
        raise ScoreError("slack_error", f"Read failed: {slack_error_text(e)}")
    return resp.get("messages", [])


def lookup_score(video_id: str, *, oldest: float | None = None) -> float | None:
    """Scan #output-port for an existing '<videoId>: 0.86' reply."""
    return parse_score_from_messages(video_id, channel_history(oldest=oldest))


def wait_for_score(video_id: str, since: float) -> float:
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        score = lookup_score(video_id, oldest=since)
        if score is not None:
            return score
    # Agent often lands just after timeout, or a prior run already posted the score.
    score = lookup_score(video_id)
    if score is not None:
        return score
    raise ScoreError("timeout", f"No reply in #output-port within {TIMEOUT:.0f}s.")


def get_score(video_id: str) -> float:
    with lock_for(f"score:{video_id}"):
        if video_id in _cache:
            return _cache[video_id]
        # Reuse a score already in Slack (e.g. previous timeout that later completed).
        existing = lookup_score(video_id)
        if existing is not None:
            _cache[video_id] = existing
            return existing
        transcript = fetch_transcript(video_id)
        # Slight back-off so clock skew can't hide a fast reply.
        since = time.time() - 2
        post_transcript(video_id, transcript)
        score = wait_for_score(video_id, since)
        _cache[video_id] = score
        return score


def split_notes_and_translation(body: str) -> tuple[str, str]:
    parts = TRANSLATION_SPLIT.split(body, maxsplit=1)
    if len(parts) == 1:
        return body.strip(), ""
    notes = parts[0].strip()
    translation = parts[1].strip()
    return notes, translation


def parse_notes_from_messages(video_id: str, messages: list) -> dict | None:
    header = re.compile(
        rf"^\s*NOTES\s+{re.escape(video_id)}\b[^\n]*\n?(.*)$",
        re.IGNORECASE | re.DOTALL,
    )
    for msg in messages:
        text = msg.get("text") or ""
        m = header.match(text)
        if not m:
            continue
        body = (m.group(1) or "").strip()
        if not body:
            continue
        notes, translation = split_notes_and_translation(body)
        return {
            "notes": notes,
            "translation": translation,
            "raw": text.strip(),
        }
    return None


def lookup_notes(video_id: str, *, oldest: float | None = None) -> dict | None:
    return parse_notes_from_messages(video_id, channel_history(oldest=oldest))


def wait_for_notes(video_id: str, since: float) -> dict:
    deadline = time.time() + NOTES_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        found = lookup_notes(video_id, oldest=since)
        if found is not None:
            return found
    found = lookup_notes(video_id)
    if found is not None:
        return found
    raise ScoreError(
        "timeout",
        f"No NOTES reply in #output-port within {NOTES_TIMEOUT:.0f}s. "
        "Ensure the Slack agent answers NOTES requests with study notes + translation.",
    )


def get_notes(video_id: str, lang: str) -> dict:
    cache_key = f"{video_id}:{lang}"
    with lock_for(f"notes:{cache_key}"):
        if cache_key in _notes_cache:
            return _notes_cache[cache_key]
        existing = lookup_notes(video_id)
        if existing is not None:
            result = {**existing, "lang": lang, "videoId": video_id}
            _notes_cache[cache_key] = result
            return result
        transcript = fetch_transcript(video_id)
        since = time.time() - 2
        post_notes_request(video_id, transcript, lang)
        found = wait_for_notes(video_id, since)
        result = {**found, "lang": lang, "videoId": video_id}
        _notes_cache[cache_key] = result
        return result


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(
                200,
                {
                    "ok": True,
                    "slack_token": bool(SLACK_BOT_TOKEN.strip()),
                    "notes_default_lang": NOTES_DEFAULT_LANG,
                },
            )

        qs = parse_qs(url.query)
        video_id = qs.get("v", [""])[0]
        if url.path in ("/score", "/notes") and not VIDEO_ID_RE.match(video_id):
            return self._send(
                400, {"ok": False, "error": "bad_request", "message": "Invalid video id."}
            )

        if url.path == "/score":
            try:
                score = get_score(video_id)
                self.log_message("%s score -> %.3f", video_id, score)
                return self._send(200, {"ok": True, "score": score})
            except ScoreError as e:
                self.log_message("%s score -> %s: %s", video_id, e.code, e.message)
                return self._send(200, {"ok": False, "error": e.code, "message": e.message})

        if url.path == "/notes":
            lang = (qs.get("lang", [NOTES_DEFAULT_LANG])[0] or NOTES_DEFAULT_LANG).lower()
            if not LANG_RE.match(lang):
                return self._send(
                    400,
                    {"ok": False, "error": "bad_request", "message": "Invalid lang code."},
                )
            try:
                result = get_notes(video_id, lang)
                self.log_message(
                    "%s notes/%s -> %d chars",
                    video_id,
                    lang,
                    len(result.get("raw") or ""),
                )
                return self._send(
                    200,
                    {
                        "ok": True,
                        "videoId": result["videoId"],
                        "lang": result["lang"],
                        "notes": result["notes"],
                        "translation": result["translation"],
                        "raw": result["raw"],
                    },
                )
            except ScoreError as e:
                self.log_message("%s notes -> %s: %s", video_id, e.code, e.message)
                return self._send(200, {"ok": False, "error": e.code, "message": e.message})

        return self._send(404, {"ok": False, "error": "not_found"})


def main():
    if not SLACK_BOT_TOKEN.strip():
        print("WARNING: SLACK_BOT_TOKEN is not set; add it to server/.env")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Listening on http://127.0.0.1:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
