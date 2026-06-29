# Lohith's Pit Wall

A personal Formula 1 2026 dashboard — dark-themed, auto-updating standings, season calendar,
countdown to the next Grand Prix, and a full-screen **Live / Replay** mode powered by FastF1.

## What it is

A single static HTML file (`lohith_f1_dashboard.html`) plus a small Python data pipeline.
The dashboard runs anywhere static files do (GitHub Pages, Vercel, or just opened locally).
Live timing and race replay need an **optional local server** — see below.

## Architecture

| Layer | File | Runs on | Notes |
|---|---|---|---|
| Dashboard UI | `lohith_f1_dashboard.html` | Static host / browser | Vanilla HTML/CSS/JS, no build step |
| Standings data | `standings.json` | Committed by CI | Drivers, constructors, calendar, podium, fastest lap, fastest pit stop |
| Data fetcher | `fetch_standings.py` | GitHub Actions (daily cron) | Pulls from Ergast API (jolpi.ca) → writes `standings.json` |
| Live/Replay server | `live_server.py` | **Local only** | FastAPI + FastF1; serves live timing + replay telemetry on `localhost:8765` |

The dashboard hydrates from `standings.json` on load and silently falls back to a static
snapshot if it's missing. Live/Replay mode degrades gracefully to a "start the server"
message when `live_server.py` isn't running — so the deployed static site is never broken.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync
```

## Updating standings

```bash
uv run fetch_standings.py
```

Writes the latest standings to `standings.json`. This runs automatically via GitHub Actions
(`.github/workflows/`) twice daily, so manual runs are only needed for a fresh local snapshot.

## Live & Replay mode (local)

Live timing and race replay require the local server (browsers can't talk to F1's timing
feed or run FastF1 directly):

```bash
uv run live_server.py
```

This starts a FastAPI server on `http://localhost:8765`. Then open the dashboard and click
the **LIVE** button (top-right):

- **LIVE tab** — real-time timing tower during a practice / qualifying / sprint / race session.
  Off-session it shows "no session running"; with the server off it shows a start command.
- **REPLAY tab** — pick any completed session this season, load it, and watch an animated
  playback of car positions on the circuit map with play / pause / scrub / speed controls.

> First replay load for an uncached session downloads telemetry from F1's API (can take a
> while and be large); subsequent loads are served from `.fastf1-cache/` and are fast.

### Endpoints (for reference)

| Endpoint | Returns |
|---|---|
| `GET /session` | Current session status |
| `GET /live` | Live timing table (best-effort) |
| `GET /replay/sessions` | Completed sessions this season |
| `GET /replay/{year}/{round}/{session}/frames` | Per-frame car positions for playback |

## Deployment

Deploy `lohith_f1_dashboard.html` + `standings.json` to any static host. The live/replay
server is **not** deployed — it's a personal tool you run locally on race weekends.

## Roadmap

- **v2** — race-winner prediction model trained on FastF1 historical data.
