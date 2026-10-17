"""Local bridge between the Chrome extension and the Slack agent.

GET /score?v=<videoId>
  1. Fetches the video's transcript with youtube-transcript-api.
  2. Uploads it as <videoId>.txt to #new-channel with the message "<videoId>".
  3. Polls #output-port for a reply of the form "<videoId>: 0.86".
  4. Returns {"ok": true, "score": 0.86} or {"ok": false, "error": ..., "message": ...}.
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
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "2.5"))
TIMEOUT = float(os.environ.get("TIMEOUT", "15"))

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

slack = WebClient(token=SLACK_BOT_TOKEN)
ytt = YouTubeTranscriptApi()

# Scores already received, so revisiting a video doesn't re-query the agent.
_cache: dict[str, float] = {}
# One lock per video so duplicate requests (e.g. two tabs) share one Slack round trip.
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


def wait_for_score(video_id: str, since: float) -> float:
    pattern = re.compile(rf"(?<![\w-]){re.escape(video_id)}\s*:\s*(\d*\.?\d+)")
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            resp = slack.conversations_history(
                channel=OUTPUT_CHANNEL, oldest=f"{since:.6f}", limit=100
            )
        except SlackApiError as e:
            raise ScoreError("slack_error", f"Read failed: {slack_error_text(e)}")
        # Messages come newest first; take the newest matching reply.
        for msg in resp.get("messages", []):
            m = pattern.search(msg.get("text", ""))
            if m:
                score = float(m.group(1))
                if 0.0 <= score <= 1.0:
                    return score
    raise ScoreError("timeout", f"No reply in #output-port within {TIMEOUT:.0f}s.")


def get_score(video_id: str) -> float:
    with _locks_guard:
        lock = _video_locks.setdefault(video_id, threading.Lock())
    with lock:
        if video_id in _cache:
            return _cache[video_id]
        transcript = fetch_transcript(video_id)
        # Slight back-off so clock skew can't hide a fast reply.
        since = time.time() - 2
        post_transcript(video_id, transcript)
        score = wait_for_score(video_id, since)
        _cache[video_id] = score
        return score


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
            return self._send(200, {"ok": True, "slack_token": bool(SLACK_BOT_TOKEN)})
        if url.path != "/score":
            return self._send(404, {"ok": False, "error": "not_found"})

        video_id = parse_qs(url.query).get("v", [""])[0]
        if not VIDEO_ID_RE.match(video_id):
            return self._send(400, {"ok": False, "error": "bad_request", "message": "Invalid video id."})
        try:
            score = get_score(video_id)
            self.log_message("%s -> %.3f", video_id, score)
            self._send(200, {"ok": True, "score": score})
        except ScoreError as e:
            self.log_message("%s -> %s: %s", video_id, e.code, e.message)
            self._send(200, {"ok": False, "error": e.code, "message": e.message})


def main():
    if not SLACK_BOT_TOKEN:
        print("WARNING: SLACK_BOT_TOKEN is not set; add it to server/.env")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Listening on http://127.0.0.1:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
