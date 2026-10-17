# YouTube AI Detector

Chrome extension + local server. When you open a YouTube video, the transcript is
uploaded to Slack `#new-channel` as `<videoId>.txt` with the message `<videoId>`.
The Slack agent replies in `#output-port` with `<videoId>: 0.86`, and a floating
badge on the video page shows the score (green = likely human, red = likely AI).

Use **Notes** on the badge to ask the Slack/Grammarly agent for study notes
(event details, main points, tips) **and a translation**. The reply is saved in
the extension’s Notes section.

## 1. Start the local server

```bash
cd server
cp .env.example .env        # then paste your xoxb- bot token into .env
.venv/bin/python server.py  # listens on http://127.0.0.1:8765
```

First time on a new machine: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

The Slack bot needs the scopes `chat:write`, `files:write` and `channels:history`
(`groups:history` for private channels), and must be invited to both channels.

## 2. Load the extension

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and pick the `extension/` folder (or **Reload** after updates).
3. Open any YouTube video. The badge appears in the top-right corner.
4. Click **Notes**, pick a translation language, then **Generate notes**.

## Behavior

- Score checks every 1.5 s for the agent's reply and gives up after 60 s (`POLL_INTERVAL` / `TIMEOUT`).
- Notes waits up to 120 s (`NOTES_TIMEOUT`) for a `NOTES <videoId>` reply with markdown including `## Translation`.
- If a score/notes reply was already posted to `#output-port`, it is reused instead of re-uploading.
- Scores are cached in memory while the server runs; notes are also cached per `videoId:lang`.
- Saved notes persist in `chrome.storage.local` (Saved list in the Notes panel).
- Works for `/watch` and `/shorts` pages, including in-app navigation between videos.

### Slack agent contract for notes

**Request** (file upload comment on `#new-channel`):

```text
NOTES <videoId> lang=<xx>
…instructions…
```

**Reply** on `#output-port` (must start with `NOTES <videoId>`):

```markdown
NOTES <videoId>
## Event details
...
## Main points
...
## Tips
...
## Translation
...
```

The scoring agent that only posts `<videoId>: 0.86` must also handle `NOTES` requests
(or a separate agent in the same channels).

Check the server:

```bash
curl http://127.0.0.1:8765/health
curl "http://127.0.0.1:8765/score?v=jNQXAC9IVRw"
curl "http://127.0.0.1:8765/notes?v=jNQXAC9IVRw&lang=es"
```
