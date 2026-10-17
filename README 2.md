# YouTube AI Detector

Chrome extension + local server. When you open a YouTube video, the transcript is
uploaded to Slack `#new-channel` as `<videoId>.txt` with the message `<videoId>`.
The Slack agent replies in `#output-port` with `<videoId>: 0.86`, and a floating
badge on the video page shows the score (green = likely human, red = likely AI).

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
2. Click **Load unpacked** and pick the `extension/` folder.
3. Open any YouTube video. The badge appears in the top-right corner.

## Behavior

- Checks every 2.5 s for the agent's reply and gives up after 15 s (`POLL_INTERVAL` / `TIMEOUT` in `.env`).
- Scores are cached in memory while the server runs, so a revisited video isn't re-sent.
- Works for `/watch` and `/shorts` pages, including in-app navigation between videos.
- Uses the English transcript if there is one, otherwise the first available language.

Check the server: `curl http://127.0.0.1:8765/health`, then `curl "http://127.0.0.1:8765/score?v=jNQXAC9IVRw"`.
